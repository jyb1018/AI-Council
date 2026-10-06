"""Official CLI adapters. Credentials stay in each provider's own client."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import ProviderConfig, Settings
from .models import CouncilError
from .process import classify_error, run_process
from .prompts import Prompt


@dataclass
class Reply:
    data: dict
    usage: dict = field(default_factory=dict)
    reported_model: str | None = None


def json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    elif text.startswith("```\n") and text.endswith("```"):
        text = text[4:-3].strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise CouncilError("protocol", "CLI/model did not return a valid JSON object.") from exc
    if not isinstance(value, dict):
        raise CouncilError("protocol", "Expected a JSON object, not a scalar or array.")
    return value


def normalized_usage(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    mapping = {
        "input_tokens": ("input_tokens",), "output_tokens": ("output_tokens",),
        "cached_input_tokens": ("cached_input_tokens", "cache_read_input_tokens", "cache_read_tokens"),
    }
    result = {}
    for target, keys in mapping.items():
        value = next((raw[k] for k in keys if k in raw), None)
        result[target] = value if type(value) is int and value >= 0 else None
    return result


def parse_response(kind: str, stdout: str) -> Reply:
    if kind == "codex":
        events = [json_object(line) for line in stdout.splitlines() if line.strip()]
        messages, completions = [], []
        for event in events:
            if event.get("type") in ("turn.failed", "error"):
                raise classify_error(json.dumps(event.get("error", event.get("message", ""))))
            item = event.get("item", {})
            if not isinstance(item, dict):
                raise CouncilError("protocol", "Invalid Codex item envelope.")
            if item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                raise CouncilError("tool_use", "Reasoning worker attempted a tool call; result rejected.")
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                messages.append(item.get("text"))
            if event.get("type") == "turn.completed":
                completions.append(event)
        if len(completions) != 1 or not messages or not isinstance(messages[-1], str):
            raise CouncilError("protocol", "Codex stream lacks exactly one successful completed turn.")
        return Reply(json_object(messages[-1]), normalized_usage(completions[-1].get("usage")))
    if kind == "antigravity":
        events = [json_object(line) for line in stdout.splitlines() if line.strip()]
        if any(isinstance(e.get("step_update"), dict) and e["step_update"].get("step_type") == "tool" for e in events):
            raise CouncilError("tool_use", "Antigravity used a tool; result rejected. Review its local permissions.")
        results = [e.get("result") for e in events if e.get("event") == "result"]
        if len(results) != 1 or not isinstance(results[0], dict):
            raise CouncilError("protocol", "Antigravity stream lacks exactly one terminal result.")
        envelope = results[0]
        if envelope.get("status") != "SUCCESS":
            raise classify_error(str(envelope.get("error", "")))
        raw = envelope.get("structured_output", envelope.get("response"))
    elif kind == "claude":
        envelope = json_object(stdout)
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise classify_error(str(envelope.get("result", envelope.get("errors", ""))))
        raw = envelope.get("structured_output", envelope.get("result"))
    else:
        raise CouncilError("config", "Unknown provider parser.")
    data = raw if isinstance(raw, dict) else json_object(raw) if isinstance(raw, str) else None
    if data is None:
        raise CouncilError("protocol", "CLI result contains no structured answer.")
    model = envelope.get("model")
    return Reply(data, normalized_usage(envelope.get("usage")), model if isinstance(model, str) else None)


def build_command(config: ProviderConfig, prompt: Prompt, workspace: Path, binary: str) -> tuple[list[str], str]:
    schema_path = workspace / "schema.json"
    schema_path.write_text(json.dumps(prompt.schema), encoding="utf-8")
    if config.kind == "codex":
        args = [binary, "-a", "never", "exec", "--ignore-user-config", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                "-c", 'forced_login_method="chatgpt"', "-c", "features.shell_tool=false",
                "-c", "features.unified_exec=false", "-c", 'web_search="disabled"',
                "--output-schema", str(schema_path)]
        if config.model:
            args += ["--model", config.model]
        if config.reasoning_effort:
            args += ["-c", "model_reasoning_effort=" + json.dumps(config.reasoning_effort)]
        return args + ["-"], prompt.render()
    if config.kind == "claude":
        args = [binary, "-p", "--safe-mode", "--tools", "", "--disallowedTools", "mcp__*",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--setting-sources", "", "--no-session-persistence", "--max-turns", "2",
                "--output-format", "json", "--json-schema", json.dumps(prompt.schema)]
        if config.model:
            args += ["--model", config.model]
        if config.reasoning_effort:
            args += ["--effort", config.reasoning_effort]
        return args, prompt.render()
    args = [binary, "--input-format", "stream-json", "--output-format", "stream-json",
            "--json-schema", str(schema_path)]
    if config.model:
        args += ["--model", config.model]
    if config.reasoning_effort:
        args += ["--effort", config.reasoning_effort]
    stdin = json.dumps({"event": "user", "message": {"content": prompt.render()}}, ensure_ascii=False) + "\n"
    return args, stdin


class CLIProvider:
    def __init__(self, name: str, config: ProviderConfig, settings: Settings):
        self.name, self.config, self.settings = name, config, settings
        self._ready = False
        self.binary = config.binary

    async def inspect(self) -> dict:
        path = shutil.which(str(Path(self.config.binary).expanduser()))
        info = {"provider": self.name, "kind": self.config.kind, "enabled": self.config.enabled,
                "available": bool(path), "compatible": False, "subscription_auth": "unknown",
                "subscription_confirmed": self.config.subscription_confirmed,
                "requested_model": self.config.model, "remaining_provider_quota": None}
        if not path:
            return info
        self.binary = path
        with tempfile.TemporaryDirectory(prefix="council-check-") as directory:
            cwd = Path(directory)
            help_args = [path, "exec", "--help"] if self.config.kind == "codex" else [path, "--help"]
            help_result = await run_process(help_args, cwd=cwd, timeout=15, max_bytes=262144)
            help_text = help_result.stdout + help_result.stderr
            required = {
                "codex": ("--ignore-user-config", "--ephemeral", "--output-schema", "--json"),
                "claude": ("--safe-mode", "--tools", "--strict-mcp-config", "--no-session-persistence", "--json-schema"),
                "antigravity": ("--input-format", "--output-format", "--json-schema"),
            }[self.config.kind]
            info["missing_flags"] = [flag for flag in required if flag not in help_text]
            info["compatible"] = help_result.returncode == 0 and not info["missing_flags"]
            version = await run_process([path, "--version"], cwd=cwd, timeout=15, max_bytes=32768)
            match = re.search(r"\d+\.\d+\.\d+(?:[-+][\w.-]+)?", version.stdout + version.stderr)
            info["version"] = match.group() if match else "unknown"
            if self.config.kind == "codex":
                auth = await run_process([path, "login", "status"], cwd=cwd, timeout=15, max_bytes=32768)
                info["subscription_auth"] = "verified" if auth.returncode == 0 and "chatgpt" in (auth.stdout + auth.stderr).lower() else "unverified"
            elif self.config.kind == "claude":
                auth = await run_process([path, "auth", "status"], cwd=cwd, timeout=15, max_bytes=32768)
                data = json_object(auth.stdout) if auth.returncode == 0 else {}
                info["subscription_auth"] = "verified" if data.get("loggedIn") and data.get("authMethod") == "claude.ai" else "unverified"
            else:
                info["warning"] = "Antigravity inherits local tools/configuration; authentication and extra-credit settings need user verification."
        return info

    async def prepare(self) -> None:
        if self._ready:
            return
        if not self.config.subscription_confirmed:
            raise CouncilError("billing_consent", "Verify subscription login and disable extra paid usage, then set subscription_confirmed=true.")
        if self.config.kind == "antigravity" and not self.config.allow_inherited_tools:
            raise CouncilError("tool_policy", "Antigravity requires explicit allow_inherited_tools=true after auditing local CLI configuration.")
        info = await self.inspect()
        if not info["available"]:
            raise CouncilError("unavailable", "Install the official CLI and put its executable on PATH.")
        if not info["compatible"]:
            raise CouncilError("unsupported_cli", "CLI lacks required capabilities. Run ai-council doctor and update the CLI.")
        if info["subscription_auth"] == "unverified":
            raise CouncilError("authentication", "A verified subscription login is required; API-key authentication is refused.")
        self._ready = True

    async def invoke(self, prompt: Prompt) -> Reply:
        await self.prepare()
        with tempfile.TemporaryDirectory(prefix="council-worker-") as directory:
            cwd = Path(directory)
            args, stdin = build_command(self.config, prompt, cwd, self.binary)
            result = await run_process(args, stdin=stdin, cwd=cwd, timeout=self.config.timeout_seconds,
                                       max_bytes=self.settings.max_output_bytes)
            if result.returncode:
                raise classify_error(result.stderr + "\n" + result.stdout)
            return parse_response(self.config.kind, result.stdout)


class MockProvider:
    """Deterministic simulation, never an imitation of real provider results."""
    def __init__(self, name: str, config: ProviderConfig, settings: Settings):
        self.name, self.config, self.settings = name, config, settings

    async def inspect(self) -> dict:
        return {"provider": self.name, "kind": "mock", "available": True, "compatible": True,
                "subscription_auth": "simulation", "remaining_provider_quota": None}

    async def invoke(self, prompt: Prompt) -> Reply:
        position = {"answer": f"[SIMULATED {prompt.alias}] Start with a small, reversible design.",
                    "rationale": ["[SIMULATED] Keep responsibilities explicit."],
                    "assumptions": ["[SIMULATED] Single-user workload."],
                    "risks": ["[SIMULATED] Measure real provider quality separately."],
                    "confidence": 0.5, "supporting_sources": []}
        if prompt.phase == "critique":
            data = {"critiques": [{"target": item["id"], "strength": "[SIMULATED] Clear scope.",
                    "weakness": "[SIMULATED] Failure recovery needs testing.",
                    "recommendation": "[SIMULATED] Add cancellation and restart tests.", "severity": "medium"}
                    for item in prompt.data["candidates"]]}
        elif prompt.phase == "revision":
            data = {**position, "changes": ["[SIMULATED] Add explicit recovery tests."]}
        elif prompt.phase == "synthesis":
            data = {"answer": "[SIMULATED] Use the reversible design and validate failure recovery.",
                    "agreements": ["[SIMULATED] Prefer explicit boundaries."],
                    "disagreements": ["[SIMULATED] Real model disagreement has not been evaluated."],
                    "next_steps": ["[SIMULATED] Run a live smoke test after official CLI login."],
                    "limitations": ["No real models, subscriptions, or external facts were tested."],
                    "confidence": 0.5, "supporting_sources": []}
        else:
            data = position
        return Reply(data, normalized_usage({}), "mock-v1")


def make_providers(settings: Settings) -> dict:
    return {name: (MockProvider if cfg.kind == "mock" else CLIProvider)(name, cfg, settings)
            for name, cfg in settings.providers.items()}
