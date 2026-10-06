import json
import os
import signal
import sys
import asyncio

import pytest

from ai_council.config import ProviderConfig, demo_settings
from ai_council.models import CouncilError
from ai_council.process import ProcessResult, classify_error, run_process, worker_environment
from ai_council.prompts import Prompt
from ai_council.providers import CLIProvider, build_command, json_object, normalized_usage, parse_response


def test_json_and_usage():
    assert json_object('```json\n{"ok":true}\n```') == {"ok": True}
    for text in ('[]', 'prefix {"x":1}', '{} trailing', ''):
        with pytest.raises(CouncilError):
            json_object(text)
    assert normalized_usage({"input_tokens": -1, "output_tokens": True})["input_tokens"] is None
    assert normalized_usage({"output_tokens": True})["output_tokens"] is None
    assert normalized_usage({"cache_read_tokens": 42})["cached_input_tokens"] == 42


@pytest.mark.parametrize("kind,body", [
    ("codex", '\n'.join([json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok":true}'}}), json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12}})])),
    ("claude", json.dumps({"type": "result", "subtype": "success", "structured_output": {"ok": True}, "usage": {"input_tokens": 12}})),
    ("antigravity", json.dumps({"event": "result", "result": {"status": "SUCCESS", "structured_output": {"ok": True}, "usage": {"input_tokens": 12}}})),
])
def test_official_envelopes(kind, body):
    reply = parse_response(kind, body)
    assert reply.data == {"ok": True}
    assert reply.usage["input_tokens"] == 12
    assert reply.usage["output_tokens"] is None


@pytest.mark.parametrize("kind,body,code", [
    ("codex", '{"type":"turn.failed","error":{"message":"429"}}', "quota"),
    ("codex", '{"type":"item.started","item":{"type":"command_execution"}}', "tool_use"),
    ("codex", '{"type":"thread.started"}', "protocol"),
    ("claude", '{"subtype":"error","result":"not logged in"}', "authentication"),
    ("claude", '{"subtype":"success"}', "protocol"),
    ("antigravity", '{"event":"step_update","step_update":{"step_type":"tool"}}', "tool_use"),
    ("antigravity", '{"event":"result","result":{"status":"ERROR","error":"quota"}}', "quota"),
    ("antigravity", '{}', "protocol"),
])
def test_provider_failure_envelopes(kind, body, code):
    with pytest.raises(CouncilError) as exc:
        parse_response(kind, body)
    assert exc.value.code == code


@pytest.mark.parametrize("kind", ["codex", "claude", "antigravity"])
def test_command_uses_stdin_and_safe_flags(tmp_path, kind):
    prompt = Prompt("position", 0, "A", {"question": "UNIQUE SECRET $(touch /tmp/should-not-exist)", "sources": []})
    cfg = ProviderConfig(kind=kind, model="configured-model")
    argv, stdin = build_command(cfg, prompt, tmp_path, "/bin/official-cli")
    assert not any("UNIQUE SECRET" in part for part in argv)
    assert "UNIQUE SECRET" in stdin
    assert "--model" in argv and "configured-model" in argv
    assert (tmp_path / "schema.json").exists()
    if kind == "codex":
        assert "--ignore-user-config" in argv and "--ephemeral" in argv
        assert 'forced_login_method="chatgpt"' in argv
    elif kind == "claude":
        assert argv[argv.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in argv and "--safe-mode" in argv
    else:
        assert json.loads(stdin)["event"] == "user"
        assert "--yolo" not in argv and "-p" not in argv


def test_no_credentials_or_recursive_config_inherited(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "AWS_SECRET_ACCESS_KEY", "AI_COUNCIL_CONFIG"):
        monkeypatch.setenv(key, "secret")
        assert key not in worker_environment()
    assert worker_environment()["AI_COUNCIL_WORKER"] == "1"
    assert classify_error("PRIVATE SECRET").as_dict()["message"].find("PRIVATE") == -1


async def test_process_stdin_no_shell(tmp_path):
    payload = '한국어 $(touch not-created); " & '
    result = await run_process([sys.executable, "-c", "import sys;print(sys.stdin.read(),end='')"], stdin=payload, cwd=tmp_path, timeout=2)
    assert result.stdout == payload
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("code,expected", [("import time;time.sleep(30)", "timeout"), ("print('x'*10000)", "output_limit"), ("import sys;sys.stderr.write('x'*10000)", "output_limit")])
async def test_bounded_process(tmp_path, code, expected):
    with pytest.raises(CouncilError) as exc:
        await run_process([sys.executable, "-c", code], cwd=tmp_path, timeout=0.2 if expected == "timeout" else 3, max_bytes=1000)
    assert exc.value.code == expected


async def test_cancellation_terminates_process_group(tmp_path):
    pid_file = tmp_path / "pid"
    code = "import os,time,pathlib; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(30)"
    task = asyncio.create_task(run_process([sys.executable, "-c", code], cwd=tmp_path, timeout=60))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGCONT)


async def test_descendant_retaining_pipe_is_killed(tmp_path):
    code = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"
    with pytest.raises(CouncilError) as exc:
        await asyncio.wait_for(run_process([sys.executable, "-c", code], cwd=tmp_path, timeout=0.2), 3)
    assert exc.value.code == "timeout"


async def test_claude_auth_and_capability_detection(tmp_path, monkeypatch):
    from ai_council import providers
    monkeypatch.setattr(providers.shutil, "which", lambda _: "/bin/claude")
    auth_method = "claude.ai"
    calls = []
    async def fake_process(argv, **kwargs):
        calls.append(argv)
        if "--help" in argv:
            return ProcessResult("--safe-mode --tools --strict-mcp-config --no-session-persistence --json-schema", "", 0)
        if "--version" in argv:
            return ProcessResult("Claude 2.9.1", "", 0)
        return ProcessResult(json.dumps({"loggedIn": True, "authMethod": auth_method}), "", 0)
    monkeypatch.setattr(providers, "run_process", fake_process)
    cfg = ProviderConfig(kind="claude", subscription_confirmed=True)
    provider = CLIProvider("claude", cfg, demo_settings(tmp_path / "x.db"))
    assert (await provider.inspect())["subscription_auth"] == "verified"
    auth_method = "api_key"
    with pytest.raises(CouncilError) as exc:
        await provider.prepare()
    assert exc.value.code == "authentication"
    assert not any("-p" in argv for argv in calls)
