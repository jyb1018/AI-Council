from pathlib import Path
import pytest
from ai_council.config import ProviderConfig
from ai_council.providers import build_command
from ai_council.prompts import Prompt
from ai_council.capabilities import normalize_codex, normalize_claude, normalize_agy, validate_selection
from ai_council.models import CouncilError


def test_native_efforts_are_not_truncated():
    levels = ["low", "medium", "high", "xhigh", "max", "ultra", "future"]
    models = normalize_codex(
        [
            {
                "model": "test",
                "displayName": "Test",
                "supportedReasoningEfforts": [{"reasoningEffort": e} for e in levels],
            }
        ]
    )
    assert models[0]["efforts"] == levels
    assert normalize_claude([{"value": "opus", "supportedEffortLevels": levels}])[0]["efforts"] == levels
    assert normalize_claude([{"value": "haiku"}])[0]["efforts"] == []
    validate_selection(models, "test", "future")
    with pytest.raises(CouncilError):
        validate_selection(models, "test", "invented")


def test_agy_preserves_exact_variants_and_all_cli_flags():
    models = normalize_agy(
        "gemini-pro-low\tGemini Pro (Low)\ngemini-pro-high\tGemini Pro (High)\nclaude-opus-thinking\tOpus (Thinking)\n",
        "  --effort Reasoning effort for the current CLI session (low|medium|high|max)\n",
    )
    assert [m["id"] for m in models] == ["gemini-pro-low", "gemini-pro-high", "claude-opus-thinking"]
    assert all(m["efforts"] == ["low", "medium", "high", "max"] for m in models)
    assert all(m["effort_scope"] == "cli" for m in models)


@pytest.mark.parametrize("kind,level", [("codex", "ultra"), ("claude", "xhigh"), ("antigravity", "max")])
def test_effort_reaches_official_command(tmp_path: Path, kind, level):
    config = ProviderConfig(kind=kind, model="model", reasoning_effort=level)
    args, _ = build_command(
        config, Prompt("position", 0, "A", {"question": "test", "sources": []}), tmp_path, "cli"
    )
    assert (
        ('model_reasoning_effort="ultra"' in args)
        if kind == "codex"
        else (args[args.index("--effort") + 1] == level)
    )
    assert config.identity()["reasoning_effort"] == level
    assert "reasoning_effort" not in ProviderConfig(kind=kind).identity()


@pytest.mark.parametrize("kind", ["codex", "claude"])
async def test_discovery_uses_real_stdio_without_user_inference(tmp_path, kind):
    import sys
    from ai_council.capabilities import discover

    script = tmp_path / "catalog-cli"
    script.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import json,sys
for line in sys.stdin:
    m=json.loads(line)
    if 'user' in str(m.get('type','')): raise RuntimeError('inference forbidden')
    if m.get('method')=='initialize':
        out={'id':1,'result':{}}
    elif m.get('method')=='initialized': continue
    elif m.get('method')=='model/list':
        second=m['params'].get('cursor')=='page2'
        out={'id':m['id'],'result':{'data':[{'model':'second' if second else 'first','supportedReasoningEfforts':[{'reasoningEffort':'ultra'}]}],'nextCursor':None if second else 'page2'}}
    elif m.get('type')=='control_request':
        assert m['request']=={'subtype':'initialize'}
        out={'type':'control_response','response':{'request_id':'catalog','response':{'models':[{'value':'opus','supportedEffortLevels':['xhigh','max']}]}}}
    else: raise RuntimeError('unexpected request')
    print(json.dumps(out),flush=True)
"""
    )
    script.chmod(0o700)
    models = await discover(ProviderConfig(kind=kind, executable=str(script)))
    if kind == "codex":
        assert [m["id"] for m in models] == ["first", "second"]
        assert all(m["efforts"] == ["ultra"] for m in models)
    else:
        assert models[0]["efforts"] == ["xhigh", "max"]


async def test_discovery_fails_closed_and_agy_lists_variants(tmp_path, monkeypatch):
    from ai_council.capabilities import discover
    from ai_council.process import ProcessResult

    with pytest.raises(CouncilError, match="Cannot start"):
        await discover(ProviderConfig(kind="codex", executable=str(tmp_path / "missing")))

    async def fake(argv, **kwargs):
        if argv[-1] == "models":
            return ProcessResult("gemini-high\tGemini High\n", "", 0)
        return ProcessResult("", "--effort (low|medium|high|max)", 0)

    monkeypatch.setattr("ai_council.capabilities.run_process", fake)
    result = await discover(ProviderConfig(kind="antigravity"))
    assert result[0]["efforts"] == ["low", "medium", "high", "max"]
