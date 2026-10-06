"""Buzz 0.5.26 ACP bridge. Fixed channel, explicit /council triggers, no model inference here."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .models import CouncilError
from .process import run_process, worker_environment
from .web import private_json

BUZZ_ENV = {"BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY", "BUZZ_AUTH_TAG"}


def event_from_prompt(blocks: list[dict], channel: str) -> tuple[str, str]:
    # Buzz sends each semantic section as a separate ACP text block. Never infer
    # routing from quoted conversation history or a model-generated tool call.
    events = [
        b.get("text", "")
        for b in blocks
        if b.get("type") == "text" and b.get("text", "").startswith("<buzz-event type=")
    ]
    if len(events) != 1:
        raise CouncilError(
            "buzz_prompt",
            "Send one explicit /council question at a time; batched/steered events are unsupported.",
        )
    text = events[0]
    match = re.match(
        r'<buzz-event type="[^"]+">\nEvent ID: ([0-9a-f]{64})\nChannel: ([^\n]+)\nKind: [0-9]+\nFrom: [^\n]+\nTime: [^\n]+\nContent: ',
        text,
    )
    if not match or not (match[2] == channel or match[2].endswith("(#" + channel + ")")):
        raise CouncilError(
            "buzz_channel", "This event does not belong to the explicitly configured Buzz channel."
        )
    content = text[match.end() :].rsplit("\nTags: ", 1)[0]
    # Permit Buzz's leading notifying mention; never interpret embedded commands.
    content = re.sub(r"^@[^\n]+?\s+(?=/council(?:\s|$))", "", content).strip()
    if not content.startswith("/council "):
        raise CouncilError("buzz_command", "Use /council followed by your question in the configured room.")
    question = content[len("/council ") :].strip()
    if not question or len(question) > 20000:
        raise CouncilError("invalid_input", "Question must contain 1–20000 characters.")
    return match[1], question


def opinion_text(attempt: dict, identity: dict) -> str:
    phases = {
        "position": "독립 의견",
        "critique": "상호 비평",
        "revision": "답변 수정",
        "synthesis": "최종 종합",
    }
    labels = {
        "answer": "답변",
        "rationale": "근거 요약",
        "critiques": "상호 비평",
        "target": "대상",
        "strength": "장점",
        "weakness": "보완점",
        "recommendation": "권고",
        "severity": "중요도",
        "risks": "위험",
        "assumptions": "가정",
        "confidence": "자기 평가",
        "changes": "수정 사항",
        "agreements": "합의",
        "disagreements": "이견",
        "next_steps": "다음 단계",
        "limitations": "한계",
        "supporting_sources": "참고 자료",
    }
    lines = [
        f"### {attempt['provider']} · {phases.get(attempt['phase'], attempt['phase'])}",
        f"모델: {identity['model'] or 'CLI 기본'} · 추론: {identity.get('reasoning_effort', 'CLI 기본')}",
    ]
    if attempt["round"]:
        lines.append(f"{attempt['round']}라운드")
    for key, value in attempt["payload"].items():
        if value is None or value == [] or value == "":
            continue
        lines.extend(["", f"**{labels.get(key, key)}**"])
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    lines.extend(f"- {labels.get(k, k)}: {v}" for k, v in item.items())
                    lines.append("")
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(str(value))
    return "\n".join(lines)


def endpoint_config(path: Path) -> dict:
    try:
        if path.stat().st_mode & 0o077:
            raise CouncilError(
                "web_endpoint", "The endpoint descriptor must be readable only by its owner (0600)."
            )
        data = json.loads(path.read_text())
        url = urlsplit(data["url"])
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or not url.port
            or url.path
            or url.query
            or url.fragment
            or url.username
        ):
            raise ValueError("not loopback")
        if not isinstance(data["token"], str) or len(data["token"]) < 20:
            raise ValueError("invalid token")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CouncilError(
            "web_endpoint", "Start ai-council web with the matching endpoint file first."
        ) from exc


class Bridge:
    def __init__(self, endpoint: Path, channel: str, binary: str):
        self.endpoint = endpoint
        self.channel = str(uuid.UUID(channel))
        self.binary = binary
        self.sessions: dict[str, dict] = {}
        self.jobs: dict[str, asyncio.Task] = {}
        self.running: dict[str, str] = {}
        self.turn_lock = asyncio.Lock()

    def emit(self, value):
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def update(self, sid, text):
        self.emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }
        )

    async def api(self, client, path, method="GET", data=None):
        response = await client.request(method, "/api" + path, json=data)
        result = response.json()
        if not response.is_success:
            error = result.get("error", {})
            raise CouncilError(
                error.get("code", "web_error"), error.get("message", "Local Council request failed.")
            )
        return result

    async def publish(self, text, event, env):
        # Model text cannot notify other users/agents or create recursive mentions.
        text = text.replace("@", "＠").replace("nostr:", "nostr∶")
        result = await run_process(
            [
                self.binary,
                "messages",
                "send",
                "--channel",
                self.channel,
                "--reply-to",
                event,
                "--content",
                "-",
            ],
            stdin=text,
            cwd=Path.home(),
            timeout=30,
            env=env,
        )
        if result.returncode:
            raise CouncilError(
                "buzz_delivery",
                "Buzz message delivery failed. No automatic resend; completed Council steps remain available in the Web UI.",
            )
        try:
            json.loads(result.stdout)
        except ValueError as exc:
            raise CouncilError(
                "buzz_delivery",
                "Buzz returned an unrecognized delivery receipt; check the room before retrying.",
            ) from exc

    async def debate(self, sid, params):
        import fcntl

        if self.turn_lock.locked():
            raise CouncilError("busy", "Another Buzz turn is active. Wait for it to finish.")
        async with self.turn_lock:
            lock_path = self.endpoint.parent / "buzz-delivery" / (self.channel + ".lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise CouncilError("busy", "Another bridge owns this Buzz channel.") from exc
                return await self._debate(sid, params)

    async def _debate(self, sid, params):
        event, question = event_from_prompt(params.get("prompt", []), self.channel)
        endpoint = endpoint_config(self.endpoint)
        env = self.sessions[sid]
        if not env.get("BUZZ_PRIVATE_KEY") or not env.get("BUZZ_RELAY_URL"):
            raise CouncilError(
                "buzz_identity",
                "Buzz did not provide its agent identity. Configure the official Buzz MCP server for this runtime.",
            )
        async with httpx.AsyncClient(
            base_url=endpoint["url"],
            headers={"Authorization": "Bearer " + endpoint["token"]},
            timeout=70,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            # Discovery may be needed after the local Web server restarts; no inference.
            await self.api(client, "/catalog")
            receipt_path = self.endpoint.parent / "buzz-delivery" / self.channel / (event + ".json")
            receipts = set(json.loads(receipt_path.read_text())["sent"]) if receipt_path.exists() else set()
            if any(not isinstance(item, str) for item in receipts):
                raise CouncilError(
                    "buzz_receipts", "Invalid local delivery journal; inspect it before retrying."
                )
            request_key = "buzz_" + base64.urlsafe_b64encode(
                hashlib.sha256((self.channel + event).encode()).digest()
            ).decode().rstrip("=")
            try:
                started = await self.api(
                    client, "/sessions", "POST", {"question": question, "idempotency_key": request_key}
                )
            except BaseException:
                # The POST may have committed even if its response was lost.
                try:
                    await self.api(client, "/requests/cancel", "POST", {"idempotency_key": request_key})
                except Exception:
                    self.update(
                        sid, "제출 상태를 확인하지 못했습니다. Web UI에서 진행 중 토론을 확인해 주세요.\n"
                    )
                raise
            council_id = started["session_id"]
            self.running[sid] = council_id

            async def deliver(key, text):
                if key in receipts:
                    return
                # Split oversized validated replies without silently dropping content.
                parts = [text[i : i + 12000] for i in range(0, len(text), 12000)]
                for i, part in enumerate(parts):
                    part_key = f"{key}:{i}"
                    if part_key not in receipts:
                        await self.publish(part, event, env)
                        receipts.add(part_key)
                        private_json(receipt_path, {"sent": sorted(receipts)})
                receipts.add(key)
                private_json(receipt_path, {"sent": sorted(receipts)})

            try:
                await deliver(
                    "start",
                    f"AI-Council · 토론 시작\n질문: {question}\n예상 {started['planned_calls']}회 호출 · 세션 {council_id}",
                )
                ticks = 0
                while True:
                    snapshot = await self.api(client, "/sessions/" + council_id)
                    for attempt in snapshot["transcript"]:
                        if attempt["status"] != "succeeded":
                            continue
                        key = str(attempt["id"])
                        identity = snapshot["provider_configuration"][attempt["provider"]]
                        text = opinion_text(attempt, identity)
                        if key not in receipts:
                            self.update(sid, text + "\n")
                        await deliver(key, text)
                    if snapshot["status"] not in ("queued", "running"):
                        ending = f"AI-Council · {snapshot['status']} · {snapshot['attempts']}회 호출"
                        if snapshot["error"]:
                            ending += "\n" + snapshot["error"]["message"]
                        await deliver("end", ending)
                        self.update(sid, ending + "\n")
                        return {"stopReason": "end_turn"}
                    if ticks % 20 == 0:
                        self.update(
                            sid, f"진행: {snapshot['phase']} · {snapshot['successful_steps']}개 답변 완료\n"
                        )
                    ticks += 1
                    await asyncio.sleep(1)
            except BaseException:
                # A disconnected room must not leave unobserved billable workers running.
                try:
                    await self.api(client, "/sessions/" + council_id + "/cancel", "POST", {})
                except Exception:
                    self.update(
                        sid, "로컬 서버에서 취소를 확인하지 못했습니다. Web UI에서 상태를 확인해 주세요.\n"
                    )
                raise
            finally:
                self.running.pop(sid, None)

    async def handle(self, message):
        request_id = message.get("id")
        method, params = message.get("method"), message.get("params", {})
        sid = params.get("sessionId")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "ai-council", "version": "1.0.0"},
                    "agentCapabilities": {"loadSession": False, "promptCapabilities": {}},
                    "authMethods": [],
                }
            elif method == "session/new":
                sid = secrets.token_hex(16)
                env = worker_environment()
                env.update({k: v for k, v in os.environ.items() if k in BUZZ_ENV})
                for server in params.get("mcpServers", []):
                    for item in server.get("env", []):
                        if item.get("name") in BUZZ_ENV:
                            env[item["name"]] = item["value"]
                self.sessions[sid] = env
                result = {"sessionId": sid}
            elif method == "session/prompt":
                if sid not in self.sessions:
                    raise CouncilError("acp_session", "Unknown ACP session.")
                if sid in self.jobs:
                    raise CouncilError("busy", "A prompt is already active in this ACP session.")
                self.jobs[sid] = asyncio.current_task()
                try:
                    result = await self.debate(sid, params)
                finally:
                    self.jobs.pop(sid, None)
            elif method == "session/cancel":
                if sid in self.jobs:
                    self.jobs[sid].cancel()
                return
            else:
                if request_id is not None:
                    self.emit(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "Method not found"},
                        }
                    )
                return
            if request_id is not None:
                self.emit({"jsonrpc": "2.0", "id": request_id, "result": result})
        except asyncio.CancelledError:
            if request_id is not None:
                self.emit({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "cancelled"}})
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, CouncilError)
                else CouncilError(
                    "buzz_bridge", "Buzz bridge failed; inspect the local Web UI. No automatic retry."
                )
            )
            if sid:
                self.update(sid, error.message + "\n")
            if request_id is not None:
                self.emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": error.message, "data": {"code": error.code}},
                    }
                )

    async def run(self):
        reader = asyncio.StreamReader(limit=524288)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        tasks = set()
        try:
            while line := await reader.readline():
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("object required")
                except ValueError:
                    self.emit(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Invalid JSON-RPC"},
                        }
                    )
                    continue
                task = asyncio.create_task(self.handle(message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            transport.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
