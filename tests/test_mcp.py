"""Real SDK/wire test, no inference or credentials. CI installs the transport dependency."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def data(response):
    assert not response.isError
    return json.loads(next(block.text for block in response.content if block.type == "text"))


async def test_stdio_protocol_end_to_end(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    env.pop("AI_COUNCIL_WORKER", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "ai_council", "serve", "--demo", "--database", str(tmp_path / "wire.db")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"council_debate", "council_review", "council_ask", "council_status", "council_result", "council_cancel", "council_resume"} <= names
            started = data(await session.call_tool("council_debate", {"question": "Choose architecture", "participants": ["alpha", "beta", "gamma"], "idempotency_key": "wire-test"}))
            sid = started["session_id"]
            for _ in range(100):
                state = data(await session.call_tool("council_status", {"session_id": sid}))
                if state["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.02)
            assert state["status"] == "completed"
            result = data(await session.call_tool("council_result", {"session_id": sid, "transcript": True}))
            assert result["attempts"] == len(result["transcript"]) == 10
            assert result["simulated"] is True
            invalid = data(await session.call_tool("council_debate", {"question": "x", "participants": ["alpha"]}))
            assert invalid["error"]["code"] == "invalid_request"
            missing = data(await session.call_tool("council_status", {"session_id": "absent"}))
            assert missing["error"]["code"] == "not_found"
