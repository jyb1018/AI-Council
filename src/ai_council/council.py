"""Bounded blind -> critique -> revision -> synthesis orchestration."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time

from pydantic import ValidationError

from .config import Settings
from .models import SCHEMAS, CouncilError, Request
from .prompts import Prompt, packet
from .providers import make_providers
from .store import Store

logger = logging.getLogger(__name__)


class Council:
    def __init__(self, settings: Settings, *, providers: dict | None = None):
        if os.environ.get("AI_COUNCIL_WORKER") == "1":
            raise CouncilError("recursive_call", "A Council worker cannot start another Council server.")
        self.settings = settings
        self.store = Store(settings.database)
        self.providers = providers if providers is not None else make_providers(settings)
        self.tasks: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        # Different model aliases of the same client still share credentials/CLI state.
        self.provider_slots = {self._slot(name): asyncio.Semaphore(1) for name in settings.providers}
        self.closing = False

    def _slot(self, name: str) -> str:
        config = self.settings.providers[name]
        return name if config.kind == "mock" else config.kind

    def _forget(self, session_id: str, task: asyncio.Task) -> None:
        if self.tasks.get(session_id) is task:
            self.tasks.pop(session_id, None)

    def _identities(self, request: Request) -> dict:
        result = {}
        for name in request.participants:
            config = self.settings.providers.get(name)
            if config is None or not config.enabled or name not in self.providers:
                raise CouncilError("provider_disabled", "Every participant must be an enabled configured provider ID.")
            if config.kind != "mock" and not config.subscription_confirmed:
                raise CouncilError("billing_consent", "Confirm subscription authentication and extra-usage settings in local configuration first.")
            if config.kind == "antigravity" and not config.allow_inherited_tools:
                raise CouncilError("tool_policy", "Audit Antigravity configuration and explicitly acknowledge inherited tools before enabling it.")
            result[name] = config.identity()
        return result

    def _spawn(self, session_id: str) -> None:
        if self.closing:
            raise CouncilError("shutting_down", "Council is shutting down.")
        if sum(not task.done() for task in self.tasks.values()) >= self.settings.max_active_sessions:
            raise CouncilError("busy", "Too many active sessions; poll an existing session before starting another.")
        self.tasks[session_id] = asyncio.create_task(self._run(session_id), name=f"council:{session_id}")
        self.tasks[session_id].add_done_callback(lambda task: self._forget(session_id, task))

    def submit(self, request: Request) -> dict:
        identities = self._identities(request)
        existing = self.store.existing(request, identities)
        if existing:
            return {**self.store.status(existing), "reused": True}
        if request.planned_calls > self.settings.max_calls_per_session:
            raise CouncilError("session_budget", "Planned calls exceed the session budget; reduce participants/rounds.")
        if self.closing or sum(not t.done() for t in self.tasks.values()) >= self.settings.max_active_sessions:
            raise CouncilError("busy", "Council is shutting down or its active-session limit has been reached.")
        names = list(request.participants)
        secrets.SystemRandom().shuffle(names)
        aliases = {name: chr(65 + index) for index, name in enumerate(names)}
        session_id = self.store.create(request, aliases, identities)
        self._spawn(session_id)
        return {**self.store.status(session_id), "reused": False}

    async def wait(self, session_id: str) -> dict:
        task = self.tasks.get(session_id)
        if task is not None:
            await asyncio.shield(task)
        return self.store.result(session_id)

    async def cancel(self, session_id: str) -> dict:
        state = self.store.get(session_id)
        task = self.tasks.get(session_id)
        if task is not None and not task.done():
            self.store.update(session_id, status="cancelled", phase=state["phase"],
                              error={"code": "cancelled", "message": "Cancelled by the user; completed steps are retained."})
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.store.status(session_id)

    def resume(self, session_id: str, *, accept_duplicate_cost: bool = False) -> dict:
        active = self.tasks.get(session_id)
        if active is not None and not active.done():
            raise CouncilError("still_running", "Wait for cancellation/cleanup to finish before resuming this session.")
        state = self.store.get(session_id)
        if state["status"] not in ("failed", "interrupted", "cancelled"):
            raise CouncilError("not_resumable", "Only failed, interrupted, or cancelled sessions can be resumed.")
        if not accept_duplicate_cost:
            raise CouncilError("resume_consent", "Resume may repeat an already billed interrupted call; accept_duplicate_cost=true is required.")
        request = Request.model_validate(state["request"])
        if self._identities(request) != state["identities"]:
            raise CouncilError("configuration_changed", "Provider identities/models changed; create a new session instead.")
        self._spawn(session_id)
        self.store.update(session_id, status="queued", phase="queued")
        return self.store.status(session_id)

    def _validate(self, prompt: Prompt, data: dict) -> dict:
        try:
            validated = SCHEMAS[prompt.phase].model_validate(data).model_dump()
        except (ValidationError, ValueError) as exc:
            raise CouncilError("invalid_response", "Model output did not match the required stage schema.") from exc
        if prompt.phase == "critique":
            targets = [c["target"] for c in validated["critiques"]]
            expected = {c["id"] for c in prompt.data["candidates"]}
            if len(targets) != len(expected) or set(targets) != expected:
                raise CouncilError("invalid_targets", "Critiques must address each peer exactly once and never the author's own answer.")
        else:
            allowed = {source["id"] for source in prompt.data["sources"]}
            if not set(validated["supporting_sources"]).issubset(allowed):
                raise CouncilError("invalid_sources", "Response cited a source ID that was not supplied.")
        return validated

    async def _invoke(self, session_id: str, name: str, prompt: Prompt) -> dict:
        key = f"{prompt.phase}:{prompt.round}:{prompt.alias}"
        cached = self.store.successful(session_id, key)
        if cached is not None:
            return cached
        rendered = prompt.render()
        if len(rendered) > self.settings.max_prompt_chars:
            raise CouncilError("context_limit", "Stage context is too large; input is never silently truncated.")
        config = self.settings.providers[name]
        async with self.semaphore, self.provider_slots[self._slot(name)]:
            invocation_id = self.store.reserve(session_id, name, prompt.alias, prompt.phase, prompt.round,
                                               rendered, self.settings.max_calls_per_session, config.daily_call_limit)
            started = time.monotonic()
            reply = None
            try:
                reply = await self.providers[name].invoke(prompt)
                data = self._validate(prompt, reply.data)
                self.store.finish(invocation_id, status="succeeded", payload=data, usage=reply.usage,
                                  reported_model=reply.reported_model, elapsed_ms=round((time.monotonic() - started) * 1000))
                return data
            except asyncio.CancelledError:
                self.store.finish(invocation_id, status="interrupted" if self.closing else "cancelled",
                                  elapsed_ms=round((time.monotonic() - started) * 1000))
                raise
            except Exception as exc:
                error = exc if isinstance(exc, CouncilError) else CouncilError("internal", "Unexpected adapter failure; no automatic retry.")
                self.store.finish(invocation_id, status="failed", error=error.as_dict(),
                                  usage=reply.usage if reply else None,
                                  elapsed_ms=round((time.monotonic() - started) * 1000))
                raise error from exc

    async def _phase(self, session_id: str, prompts: dict[str, Prompt]) -> dict[str, dict]:
        first = next(iter(prompts.values()))
        self.store.update(session_id, status="running", phase=f"{first.phase}:{first.round}")
        names = list(prompts)
        values = await asyncio.gather(*(self._invoke(session_id, name, prompts[name]) for name in names), return_exceptions=True)
        failures = [(name, value) for name, value in zip(names, values) if isinstance(value, BaseException)]
        if failures:
            error = next((value for _, value in failures if isinstance(value, CouncilError)),
                         CouncilError("internal", "A required participant did not finish."))
            raise CouncilError(error.code, f"Stage {first.phase}:{first.round} failed for {', '.join(name for name, _ in failures)}. {error.message}")
        return dict(zip(names, values))

    async def _run(self, session_id: str) -> None:
        state = self.store.get(session_id)
        request = Request.model_validate(state["request"])
        aliases = state["aliases"]
        common = packet(request)
        try:
            async with asyncio.timeout(self.settings.session_timeout_seconds):
                positions = await self._phase(session_id, {
                    name: Prompt("position", 0, aliases[name], common) for name in request.participants
                })
                # The await above is the blind barrier. No peer receives any draft before it succeeds.
                if request.mode == "ask":
                    final = positions[request.participants[0]]
                else:
                    history = []
                    for round_number in range(1, request.rounds + 1):
                        critiques = await self._phase(session_id, {
                            name: Prompt("critique", round_number, aliases[name], {
                                **common, "candidates": [{"id": aliases[peer], "position": positions[peer]}
                                                         for peer in sorted(request.participants, key=aliases.get) if peer != name],
                            }) for name in request.participants
                        })
                        revisions = await self._phase(session_id, {
                            name: Prompt("revision", round_number, aliases[name], {
                                **common, "own_position": positions[name],
                                "received_critiques": [{"reviewer": aliases[peer], **critique}
                                    for peer, bundle in critiques.items() for critique in bundle["critiques"]
                                    if critique["target"] == aliases[name]],
                            }) for name in request.participants
                        })
                        history.append({"round": round_number, "critiques": [
                            {"reviewer": aliases[name], **bundle} for name, bundle in critiques.items()]})
                        positions = revisions
                    chair = request.chair or request.participants[0]
                    synthesized = await self._phase(session_id, {chair: Prompt("synthesis", request.rounds, aliases[chair], {
                        **common, "candidates": [{"id": aliases[name], "position": positions[name]}
                                                 for name in sorted(request.participants, key=aliases.get)],
                        "critique_history": history,
                    })})
                    final = synthesized[chair]
                self.store.update(session_id, status="completed", phase="completed", result=final)
        except TimeoutError:
            self.store.update(session_id, status="failed", phase=self.store.get(session_id)["phase"],
                              error={"code": "session_timeout", "message": "Session deadline reached; active workers were cancelled."})
        except asyncio.CancelledError:
            self.store.update(session_id, status="interrupted" if self.closing else "cancelled",
                              phase=self.store.get(session_id)["phase"],
                              error={"code": "interrupted" if self.closing else "cancelled", "message": "Execution stopped; completed steps are retained."})
            raise
        except Exception as exc:
            error = exc if isinstance(exc, CouncilError) else CouncilError("internal", "Unexpected Council error; no automatic retry.")
            logger.error("Council session failed: %s (%s)", session_id, error.code)
            self.store.update(session_id, status="failed", phase=self.store.get(session_id)["phase"], error=error.as_dict())

    async def close(self) -> None:
        self.closing = True
        pending = list(self.tasks.items())
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        for session_id, _ in pending:
            state = self.store.get(session_id)
            if state["status"] in ("queued", "running"):
                self.store.update(session_id, status="interrupted", phase=state["phase"])
        self.store.close()
