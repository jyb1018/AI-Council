"""MCP stdio endpoint. Jobs are owned by this process and survive client polling, not shutdown."""
from __future__ import annotations

from contextlib import asynccontextmanager

from pydantic import ValidationError

from .config import Settings
from .council import Council
from .models import CouncilError, Request


def create_server(settings: Settings):
    # Keep the CLI/demo and core tests usable without importing the transport dependency.
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    holder: dict[str, Council] = {}

    @asynccontextmanager
    async def lifespan(_server):
        service = Council(settings)
        holder["service"] = service
        try:
            yield service
        finally:
            await service.close()
            holder.clear()

    mcp = FastMCP(
        "AI-Council",
        instructions=("Local subscription CLI council. Submit jobs, then poll council_status and council_result. "
                      "Submission invokes external AI providers and consumes their usage limits. "
                      "Do not automatically repeat failed jobs or resume without the user's consent. "
                      "No partial transcript is exposed. Remote HTTP and browser automation are not provided."),
        lifespan=lifespan,
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    invoke = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
    change = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    def service() -> Council:
        return holder["service"]

    def submit(**kwargs) -> dict:
        try:
            return service().submit(Request.model_validate(kwargs))
        except ValidationError:
            return {"error": {"code": "invalid_request", "message": "Check request fields, limits, and participant IDs."}}
        except CouncilError as exc:
            return {"error": exc.as_dict()}

    @mcp.tool(annotations=invoke)
    async def council_debate(question: str, participants: list[str], context: str = "",
                             rounds: int = 1, chair: str | None = None,
                             sources: list[dict] | None = None, language: str = "Korean (polite)",
                             idempotency_key: str | None = None) -> dict:
        """Start blind answers -> all-peer critique -> revision -> chair synthesis; returns a session ID immediately.

        participants are configured provider IDs, not executable names. 2-6 participants, 1-3 rounds.
        Calls = N*(1+2*rounds)+1. Same idempotency key + identical request reuses a session.
        """
        return submit(question=question, participants=participants, context=context, rounds=rounds,
                      chair=chair, sources=sources or [], language=language, idempotency_key=idempotency_key)

    @mcp.tool(annotations=invoke)
    async def council_review(question: str, material: str, participants: list[str],
                             rounds: int = 1, chair: str | None = None,
                             sources: list[dict] | None = None, language: str = "Korean (polite)",
                             idempotency_key: str | None = None) -> dict:
        """Review supplied material using the full blind/critique/revision protocol. Does not read local files."""
        return submit(question=question, context=material, mode="review", participants=participants,
                      rounds=rounds, chair=chair, sources=sources or [], language=language,
                      idempotency_key=idempotency_key)

    @mcp.tool(annotations=invoke)
    async def council_ask(question: str, provider: str, context: str = "",
                          sources: list[dict] | None = None, language: str = "Korean (polite)",
                          idempotency_key: str | None = None) -> dict:
        """Start a single-provider baseline (one invocation, no debate); returns a session ID."""
        return submit(question=question, participants=[provider], context=context, mode="ask", rounds=0,
                      sources=sources or [], language=language, idempotency_key=idempotency_key)

    @mcp.tool(annotations=read)
    async def council_status(session_id: str) -> dict:
        """Get status and attempt counts without revealing sealed drafts or alias mapping."""
        try:
            return service().store.status(session_id)
        except CouncilError as exc:
            return {"error": exc.as_dict()}

    @mcp.tool(annotations=read)
    async def council_result(session_id: str, transcript: bool = False) -> dict:
        """Read the completed verdict, usage, and optionally the full transcript; incomplete drafts stay sealed."""
        try:
            return service().store.result(session_id, transcript=transcript)
        except CouncilError as exc:
            return {"error": exc.as_dict()}

    @mcp.tool(annotations=read)
    async def council_sessions(limit: int = 20) -> dict:
        """List at most 100 recent sessions in this local database."""
        return {"sessions": service().store.list_sessions(limit)}

    @mcp.tool(annotations=read)
    async def council_providers() -> dict:
        """Describe local configuration without starting a CLI, reading credentials, or guessing remaining quota."""
        return {"providers": [{"id": name, **cfg.model_dump(exclude={"executable"}),
                                "remaining_provider_quota": None}
                               for name, cfg in settings.providers.items()]}

    @mcp.tool(annotations=change)
    async def council_cancel(session_id: str) -> dict:
        """Cancel active workers, keeping completed steps; already-consumed provider usage cannot be refunded."""
        try:
            return await service().cancel(session_id)
        except CouncilError as exc:
            return {"error": exc.as_dict()}

    @mcp.tool(annotations=invoke)
    async def council_resume(session_id: str, accept_duplicate_cost: bool = False) -> dict:
        """Explicitly resume a stopped job. Interrupted calls can be billed twice; require the user's consent."""
        try:
            return service().resume(session_id, accept_duplicate_cost=accept_duplicate_cost)
        except CouncilError as exc:
            return {"error": exc.as_dict()}

    return mcp
