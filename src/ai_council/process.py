"""Bounded, cancellable POSIX subprocesses. No shell, no arbitrary inherited secrets."""
from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from .models import CouncilError

# HOME/keyring location is needed by official clients. Do not copy auth files.
ENV_ALLOWLIST = {
    "HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
    "https_proxy", "http_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
}


def worker_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in ENV_ALLOWLIST}
    env.update({"AI_COUNCIL_WORKER": "1", "NO_COLOR": "1", "TERM": "dumb"})
    return env


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    returncode: int


def classify_error(message: str) -> CouncilError:
    text = message.lower()
    if any(x in text for x in ("429", "rate limit", "quota", "usage limit", "limit reached")):
        return CouncilError("quota", "Provider usage limit reached. No automatic retry or paid fallback.")
    if any(x in text for x in ("401", "unauthorized", "authentication", "not logged", "sign in", "login")):
        return CouncilError("authentication", "Authenticate with the official CLI outside Council.")
    if any(x in text for x in ("unknown option", "unexpected argument", "unrecognized argument")):
        return CouncilError("unsupported_cli", "Installed CLI does not support the required flags.")
    return CouncilError("provider_error", "Provider failed; inspect the official CLI directly. Raw diagnostics are not stored.")


async def run_process(
    argv: list[str], *, stdin: str = "", cwd: Path, timeout: float,
    max_bytes: int = 1048576, env: dict[str, str] | None = None,
) -> ProcessResult:
    if os.name != "posix":
        raise CouncilError("platform", "v1 supports macOS/Linux/WSL, not native Windows.")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=cwd, env=env if env is not None else worker_environment(),
            start_new_session=True,
        )
    except OSError as exc:
        raise CouncilError("unavailable", "Cannot start the configured CLI executable.") from exc
    total = 0

    async def read(stream: asyncio.StreamReader) -> bytes:
        nonlocal total
        chunks = []
        while chunk := await stream.read(16384):
            total += len(chunk)
            if total > max_bytes:
                raise CouncilError("output_limit", "CLI stdout/stderr exceeded the configured byte limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    async def write() -> None:
        try:
            proc.stdin.write(stdin.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    async def terminate() -> None:
        # Kill the group even if its leader already exited but descendants retain pipes.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                await asyncio.sleep(0.1)
        await proc.wait()

    tasks = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr)),
             asyncio.create_task(write()), asyncio.create_task(proc.wait())]
    try:
        async with asyncio.timeout(timeout):
            out, err, _, code = await asyncio.gather(*tasks)
        return ProcessResult(out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"), code)
    except TimeoutError as exc:
        raise CouncilError("timeout", "CLI invocation timed out; its process group was terminated.") from exc
    finally:
        # This also covers cancellation, output overflow, and children surviving a successful parent.
        await terminate()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
