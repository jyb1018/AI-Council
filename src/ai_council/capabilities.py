"""Read installed CLI catalogs without inference; never invent supported model/effort pairs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import tempfile
from pathlib import Path

from .config import ProviderConfig
from .models import CouncilError
from .process import run_process, worker_environment


def normalize_codex(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": r["model"],
            "name": r.get("displayName", r["model"]),
            "efforts": [e["reasoningEffort"] for e in r["supportedReasoningEfforts"]],
            "default_effort": r.get("defaultReasoningEffort"),
            "effort_scope": "model",
            "hidden": r.get("hidden", False),
        }
        for r in rows
    ]


def normalize_claude(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": r["value"],
            "name": r.get("displayName", r["value"]),
            "resolved_model": r.get("resolvedModel"),
            "efforts": r.get("supportedEffortLevels", []),
            "effort_scope": "model",
        }
        for r in rows
    ]


def normalize_agy(output: str, help_text: str) -> list[dict]:
    # Antigravity currently exposes CLI-wide effort flags, not a per-model capability API.
    match = re.search(r"--effort[^\n]*?\(([a-z]+(?:\|[a-z]+)+)\)", help_text)
    if not match:
        raise CouncilError("capabilities", "Antigravity did not advertise its effort options.")
    levels = match[1].split("|")
    return [
        {
            "id": line.split("\t", 1)[0],
            "name": line.split("\t", 1)[1],
            "efforts": levels,
            "effort_scope": "cli",
        }
        for line in output.splitlines()
        if "\t" in line and re.fullmatch(r"[a-zA-Z0-9._:-]+", line.split("\t")[0])
    ]


def validate_selection(models: list[dict], model: str | None, effort: str | None) -> None:
    if model is None and effort is None:
        return
    selected = next((item for item in models if item["id"] == model), None)
    if selected is None or (effort is not None and effort not in selected["efforts"]):
        raise CouncilError(
            "unsupported_selection", "Select a model and effort advertised by the installed CLI."
        )


async def _catalog_protocol(config: ProviderConfig, cwd: Path) -> list[dict]:
    codex = config.kind == "codex"
    args = (
        [config.binary, "app-server", "--stdio"]
        if codex
        else [
            config.binary,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--safe-mode",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--no-session-persistence",
        ]
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=worker_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=1048576,
        )
    except OSError as exc:
        raise CouncilError("unavailable", "Cannot start the configured CLI for model discovery.") from exc

    async def send(value):
        proc.stdin.write((json.dumps(value) + "\n").encode())
        await proc.stdin.drain()

    rows, total, page = [], 0, 2
    try:
        async with asyncio.timeout(45):
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "ai-council-catalog", "version": "1.0.0"}},
                }
                if codex
                else {
                    "type": "control_request",
                    "request_id": "catalog",
                    "request": {"subtype": "initialize"},
                }
            )
            while line := await proc.stdout.readline():
                total += len(line)
                if total > 4 * 1048576:
                    raise CouncilError("output_limit", "Model catalog exceeded its size limit.")
                message = json.loads(line)
                if codex:
                    if message.get("error"):
                        raise CouncilError("capabilities", "Codex rejected model discovery.")
                    if message.get("id") == 1:
                        await send({"jsonrpc": "2.0", "method": "initialized"})
                        await send(
                            {
                                "jsonrpc": "2.0",
                                "id": page,
                                "method": "model/list",
                                "params": {"includeHidden": True, "limit": 100},
                            }
                        )
                    elif message.get("id") == page:
                        result = message["result"]
                        rows.extend(result["data"])
                        if not result.get("nextCursor"):
                            return normalize_codex(rows)
                        page += 1
                        await send(
                            {
                                "jsonrpc": "2.0",
                                "id": page,
                                "method": "model/list",
                                "params": {
                                    "includeHidden": True,
                                    "limit": 100,
                                    "cursor": result["nextCursor"],
                                },
                            }
                        )
                elif message.get("type") == "control_response":
                    response = message.get("response", {})
                    if response.get("request_id") == "catalog":
                        return normalize_claude(response["response"]["models"])
        raise CouncilError("capabilities", "CLI closed before returning its model catalog.")
    except TimeoutError as exc:
        raise CouncilError("capabilities", "Model discovery timed out. No inference was requested.") from exc
    except (ValueError, KeyError, TypeError, BrokenPipeError, ConnectionResetError) as exc:
        raise CouncilError("capabilities", "Installed CLI returned an unsupported catalog format.") from exc
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                await asyncio.sleep(0.1)
        await proc.wait()


async def discover(config: ProviderConfig) -> list[dict]:
    if config.kind == "mock":
        return [
            {"id": "simulated", "name": "Simulated (no inference)", "efforts": [], "effort_scope": "model"}
        ]
    with tempfile.TemporaryDirectory(prefix="council-catalog-") as directory:
        cwd = Path(directory)
        if config.kind in ("codex", "claude"):
            models = await _catalog_protocol(config, cwd)
        else:
            result = await run_process([config.binary, "models"], cwd=cwd, timeout=60)
            help_result = await run_process([config.binary, "--help"], cwd=cwd, timeout=15)
            if result.returncode or help_result.returncode:
                raise CouncilError(
                    "capabilities", "Antigravity model discovery failed; check its official CLI login."
                )
            models = normalize_agy(result.stdout, help_result.stdout + help_result.stderr)
    if not models:
        raise CouncilError("capabilities", "The installed CLI did not return any available models.")
    return models
