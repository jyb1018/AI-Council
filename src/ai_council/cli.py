"""Command-line entry point. Machine-readable output uses stdout; diagnostics use stderr."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .config import demo_settings, load_settings
from .council import Council
from .models import CouncilError, Request
from .providers import make_providers
from .store import Store


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def read_text(path: str | None, limit: int) -> str:
    if path is None:
        return ""
    with Path(path).expanduser().open(encoding="utf-8") as file:
        text = file.read(limit + 1)
    if len(text) > limit:
        raise CouncilError("input_limit", "Input file exceeds its documented size limit.")
    return text


def markdown(result: dict) -> str:
    body = result.get("result") or {}
    lines = ["# AI-Council report", "", f"Session: `{result['session_id']}`",
             f"Status: **{result['status']}**", f"Simulated: **{result.get('simulated', False)}**", "",
             str(body.get("answer", "No completed verdict is available.")), ""]
    for key in ("rationale", "agreements", "disagreements", "assumptions", "risks", "next_steps", "limitations", "supporting_sources"):
        if body.get(key):
            lines.extend([f"## {key.replace('_', ' ').title()}", ""])
            lines.extend(f"- {item}" for item in body[key])
            lines.append("")
    lines += ["## Audit", "", f"Attempts: {result['attempts']}; planned: {result['planned_calls']}.",
              "Confidence is an uncalibrated model self-report, not a probability of correctness.", ""]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ai-council", description="Local subscription-backed AI Council")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--config", help="Explicit TOML path (or AI_COUNCIL_CONFIG); no implicit cwd configuration")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the MCP stdio server")
    serve.add_argument("--demo", action="store_true", help="Use simulated providers only")
    serve.add_argument("--database", type=Path, help="Override the database path")
    web = commands.add_parser("web", help="Run the local authenticated chat/settings UI")
    web.add_argument("--demo", action="store_true")
    web.add_argument("--database", type=Path)
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--endpoint", type=Path, help="Private endpoint descriptor for the Buzz bridge")
    web.add_argument("--open", action="store_true", dest="open_browser")
    buzz = commands.add_parser("buzz-acp", help="Buzz custom ACP runtime; requires a running Web server")
    buzz.add_argument("--endpoint", type=Path, required=True)
    buzz.add_argument("--channel", required=True, help="Explicit Buzz channel UUID; other rooms are rejected")
    buzz.add_argument("--buzz-binary", default="/Applications/Buzz.app/Contents/MacOS/buzz")
    buzz_config = commands.add_parser("buzz-config", help="Print a Buzz custom harness definition (no credentials)")
    buzz_config.add_argument("--endpoint", type=Path, required=True)
    buzz_config.add_argument("--channel", required=True)
    commands.add_parser("doctor", help="Check CLI capabilities/authentication without running model inference")
    demo = commands.add_parser("demo", help="Run a labelled three-provider simulation; no account required")
    demo.add_argument("question", nargs="?", default="Should a personal state service start with SQLite?")
    demo.add_argument("--database", type=Path)
    demo.add_argument("--rounds", type=int, default=1)
    for name in ("debate", "review", "ask"):
        action = commands.add_parser(name)
        action.add_argument("question")
        action.add_argument("--participants", required=True, help="Comma-separated configured IDs; one for ask")
        action.add_argument("--chair")
        action.add_argument("--rounds", type=int, default=1)
        action.add_argument("--context-file")
        action.add_argument("--sources-file", help="JSON array of Source objects")
        action.add_argument("--language", default="Korean (polite)")
        action.add_argument("--idempotency-key")
    status = commands.add_parser("status")
    status.add_argument("session_id", nargs="?")
    result = commands.add_parser("result")
    result.add_argument("session_id")
    result.add_argument("--transcript", action="store_true")
    export = commands.add_parser("export")
    export.add_argument("session_id")
    export.add_argument("--output", required=True)
    export.add_argument("--format", choices=["json", "markdown"], default="json")
    export.add_argument("--transcript", action="store_true")
    export.add_argument("--overwrite", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("session_id")
    resume.add_argument("--accept-duplicate-cost", action="store_true")
    return root


async def doctor(settings) -> int:
    reports = []
    for name, provider in make_providers(settings).items():
        if not settings.providers[name].enabled:
            reports.append({"provider": name, "enabled": False})
            continue
        try:
            reports.append(await provider.inspect())
        except CouncilError as exc:
            reports.append({"provider": name, "error": exc.as_dict()})
    emit({"providers": reports, "inference_performed": False,
          "note": "CLI login does not verify account-level extra-credit billing; check provider settings."})
    return 0 if all(r.get("enabled") is False or (r.get("available") and r.get("compatible")) for r in reports) else 1


async def run(args, settings) -> int:
    if args.command == "web":
        from .web import serve_web
        await serve_web(settings, port=args.port,
                        endpoint=args.endpoint or settings.database.with_suffix(".web.json"),
                        open_browser=args.open_browser)
        return 0
    if args.command == "buzz-config":
        from uuid import UUID
        channel = str(UUID(args.channel))
        emit({"id": "ai-council", "label": "AI-Council", "command": sys.executable,
              "args": ["-m", "ai_council", "buzz-acp", "--endpoint", str(args.endpoint.expanduser().resolve()),
                       "--channel", channel], "env": {},
              "installHint": "Start ai-council web first; keep Buzz access owner-only."})
        return 0
    if args.command == "buzz-acp":
        from .buzz import Bridge
        await Bridge(args.endpoint.expanduser(), args.channel, args.buzz_binary).run()
        return 0
    if args.command == "doctor":
        return await doctor(settings)
    if args.command in ("status", "result", "export"):
        store = Store(settings.database, read_only=True)
        try:
            if args.command == "status":
                emit(store.status(args.session_id) if args.session_id else {"sessions": store.list_sessions()})
            else:
                result = store.result(args.session_id, transcript=args.transcript)
                if args.command == "export":
                    text = markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                    output = Path(args.output).expanduser()
                    with output.open("w" if args.overwrite else "x", encoding="utf-8") as file:
                        file.write(text)
                    emit({"output": str(output), "session_id": args.session_id})
                else:
                    emit(result)
            return 0
        finally:
            store.close()
    council = Council(settings)
    try:
        if args.command == "resume":
            started = council.resume(args.session_id, accept_duplicate_cost=args.accept_duplicate_cost)
        else:
            is_demo = args.command == "demo"
            sources_text = "" if is_demo else read_text(args.sources_file, 300000)
            request = Request(
                question=args.question,
                context="" if is_demo else read_text(args.context_file, 60000),
                participants=list(settings.providers) if is_demo else args.participants.split(","),
                chair=None if is_demo else args.chair,
                mode="debate" if is_demo else args.command,
                rounds=0 if args.command == "ask" else args.rounds,
                language="Korean (polite)" if is_demo else args.language,
                sources=json.loads(sources_text) if sources_text else [],
                idempotency_key=None if is_demo else args.idempotency_key,
            )
            started = council.submit(request)
        result = await council.wait(started["session_id"])
        emit(result)
        return 0 if result["status"] == "completed" else 1
    finally:
        await council.close()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
    def report(error: dict) -> None:
        if args.command in ("serve", "buzz-acp"):
            print(json.dumps({"error": error}), file=sys.stderr)
        else:
            emit({"error": error})

    try:
        settings = demo_settings(getattr(args, "database", None)) if (args.command == "demo" or getattr(args, "demo", False)) else load_settings(args.config)
        if getattr(args, "database", None):
            settings.database = args.database.expanduser()
        if args.command == "serve":
            from .server import create_server
            create_server(settings).run(transport="stdio")
            return 0
        return asyncio.run(run(args, settings))
    except CouncilError as exc:
        report(exc.as_dict())
        return 1
    except (ValidationError, ValueError, OSError):
        report({"code": "invalid_input", "message": "Check configuration, arguments, and input/output file paths."})
        return 2
    except ImportError:
        report({"code": "missing_dependency", "message": "Install the package dependencies before serving MCP."})
        return 2
    except KeyboardInterrupt:
        return 130
