import asyncio
import json
import sqlite3

import pytest
from pydantic import ValidationError

from ai_council.config import ProviderConfig, demo_settings, load_settings
from ai_council.council import Council
from ai_council.models import CouncilError, Request
from ai_council.providers import MockProvider
from ai_council.store import Store


@pytest.fixture
def settings(tmp_path):
    return demo_settings(tmp_path / "state.sqlite3")


def request(**kwargs):
    return Request.model_validate({"question": "Choose a reversible design.", "participants": ["alpha", "beta", "gamma"], **kwargs})


@pytest.mark.parametrize("patch", [
    {"question": " "}, {"participants": ["alpha", "alpha"]}, {"participants": ["alpha"]},
    {"rounds": 0}, {"rounds": 4}, {"chair": "absent"}, {"unexpected": True},
    {"mode": "ask"}, {"participants": ["../escape", "beta"]},
    {"sources": [{"id": "x", "title": "t", "content": "c"}, {"id": "x", "title": "t", "content": "c"}]},
])
def test_invalid_request(patch):
    with pytest.raises(ValidationError):
        request(**patch)


def test_call_count_and_ask():
    assert request().planned_calls == 10
    assert request(rounds=2).planned_calls == 16
    assert request(mode="ask", participants=["alpha"], rounds=0).planned_calls == 1


@pytest.mark.parametrize("mode,rounds,count", [("debate", 1, 10), ("debate", 2, 16), ("review", 1, 10), ("ask", 0, 1)])
async def test_complete_protocol(settings, mode, rounds, count):
    council = Council(settings)
    try:
        req = request(mode=mode, rounds=rounds, participants=["alpha"] if mode == "ask" else list(settings.providers))
        sid = council.submit(req)["session_id"]
        result = await council.wait(sid)
        assert result["status"] == "completed"
        assert result["attempts"] == result["successful_steps"] == count
        assert result["simulated"] is True
        transcript = council.store.result(sid, transcript=True)["transcript"]
        assert all(a["status"] == "succeeded" for a in transcript)
        if mode != "ask":
            critiques = [a for a in transcript if a["phase"] == "critique"]
            for entry in critiques:
                targets = [c["target"] for c in entry["payload"]["critiques"]]
                assert len(targets) == 2 and entry["alias"] not in targets
            assert "disagreements" in result["result"]
    finally:
        await council.close()


async def test_blind_barrier_anonymization_and_sealed_transcript(settings):
    started, release = asyncio.Event(), asyncio.Event()
    seen = []
    positions_started = set()
    positions_finished = set()

    class Observer(MockProvider):
        async def invoke(self, prompt):
            seen.append((self.name, prompt))
            if prompt.phase == "position":
                assert "candidates" not in prompt.data
                positions_started.add(self.name)
                if len(positions_started) == 3:
                    started.set()
                await release.wait()
                positions_finished.add(self.name)
            elif prompt.phase == "critique":
                assert len(positions_finished) == 3
                assert all(c["id"] != prompt.alias for c in prompt.data["candidates"])
                assert all(name not in json.dumps(prompt.data) for name in settings.providers)
            return await super().invoke(prompt)

    providers = {name: Observer(name, cfg, settings) for name, cfg in settings.providers.items()}
    council = Council(settings, providers=providers)
    try:
        sid = council.submit(request())["session_id"]
        await asyncio.wait_for(started.wait(), 2)
        sealed = council.store.result(sid, transcript=True)
        assert "transcript" not in sealed and "alias_to_provider" not in sealed
        assert sealed["result"] is None
        release.set()
        assert (await council.wait(sid))["status"] == "completed"
        assert len(seen) == 10
    finally:
        await council.close()


async def test_idempotency_conflict_and_reuse(settings):
    council = Council(settings)
    try:
        req = request(idempotency_key="job-123")
        first = council.submit(req)
        assert council.submit(req)["reused"]
        with pytest.raises(CouncilError, match="different request"):
            council.submit(request(idempotency_key="job-123", question="Different"))
        await council.wait(first["session_id"])
        assert council.submit(req)["session_id"] == first["session_id"]
        assert len(council.store.attempts(first["session_id"])) == 10
    finally:
        await council.close()


async def test_no_automatic_retry_and_resume_reuses_successes(settings):
    fail = True

    class Flaky(MockProvider):
        async def invoke(self, prompt):
            if self.name == "beta" and prompt.phase == "critique" and fail:
                raise CouncilError("quota", "No retry.")
            return await super().invoke(prompt)

    providers = {name: Flaky(name, cfg, settings) for name, cfg in settings.providers.items()}
    council = Council(settings, providers=providers)
    try:
        sid = council.submit(request())["session_id"]
        failed = await council.wait(sid)
        assert failed["status"] == "failed" and failed["attempts"] == 6
        assert "transcript" not in council.store.result(sid, transcript=True)
        with pytest.raises(CouncilError) as error:
            council.resume(sid)
        assert error.value.code == "resume_consent"
        fail = False
        council.resume(sid, accept_duplicate_cost=True)
        complete = await council.wait(sid)
        assert complete["status"] == "completed" and complete["attempts"] == 11
        assert complete["successful_steps"] == 10
    finally:
        await council.close()


@pytest.mark.parametrize("fault,expected", [("source", "invalid_sources"), ("self", "invalid_targets"), ("schema", "invalid_response")])
async def test_invalid_output_fails_closed(settings, fault, expected):
    class Invalid(MockProvider):
        async def invoke(self, prompt):
            reply = await super().invoke(prompt)
            if fault == "source" and prompt.phase == "position":
                reply.data["supporting_sources"] = ["fabricated"]
            if fault == "self" and prompt.phase == "critique":
                reply.data["critiques"][0]["target"] = prompt.alias
            if fault == "schema" and prompt.phase == "position":
                reply.data["confidence"] = float("nan")
            return reply
    council = Council(settings, providers={n: Invalid(n, c, settings) for n, c in settings.providers.items()})
    try:
        sid = council.submit(request())["session_id"]
        result = await council.wait(sid)
        assert result["status"] == "failed" and result["error"]["code"] == expected
    finally:
        await council.close()


async def test_planned_and_daily_budgets(settings):
    settings.max_calls_per_session = 9
    council = Council(settings)
    try:
        with pytest.raises(CouncilError) as exc:
            council.submit(request())
        assert exc.value.code == "session_budget"
        settings.max_calls_per_session = 32
        settings.providers["alpha"].daily_call_limit = 1
        sid = council.submit(request())["session_id"]
        result = await council.wait(sid)
        assert result["status"] == "failed" and result["error"]["code"] == "daily_budget"
        assert sum(a["provider"] == "alpha" for a in council.store.attempts(sid)) == 1
    finally:
        await council.close()


async def test_retry_budget_counts_failed_attempts(settings):
    settings.max_calls_per_session = 10
    class Broken(MockProvider):
        async def invoke(self, prompt):
            raise CouncilError("quota", "limited")
    council = Council(settings, providers={n: Broken(n, c, settings) for n, c in settings.providers.items()})
    try:
        sid = council.submit(request())["session_id"]
        for _ in range(4):
            await council.wait(sid)
            council.resume(sid, accept_duplicate_cost=True)
        result = await council.wait(sid)
        assert result["attempts"] == 10
        assert result["status"] == "failed"
    finally:
        await council.close()


async def test_cancel_restart_and_explicit_resume(settings):
    entered = asyncio.Event()
    class Slow(MockProvider):
        async def invoke(self, prompt):
            entered.set()
            await asyncio.sleep(30)
    council = Council(settings, providers={n: Slow(n, c, settings) for n, c in settings.providers.items()})
    sid = council.submit(request())["session_id"]
    await entered.wait()
    await council.close()
    reopened = Council(settings)
    try:
        assert reopened.store.status(sid)["status"] == "interrupted"
        assert not reopened.tasks
        reopened.resume(sid, accept_duplicate_cost=True)
        assert (await reopened.wait(sid))["status"] == "completed"
    finally:
        await reopened.close()


async def test_cancel_before_task_starts_and_configuration_change(settings):
    council = Council(settings)
    try:
        sid = council.submit(request())["session_id"]
        assert (await council.cancel(sid))["status"] == "cancelled"
        settings.providers["alpha"].model = "another-model"
        with pytest.raises(CouncilError) as exc:
            council.resume(sid, accept_duplicate_cost=True)
        assert exc.value.code == "configuration_changed"
    finally:
        await council.close()


async def test_busy_and_context_limit(settings):
    settings.max_active_sessions = 1
    council = Council(settings)
    try:
        sid = council.submit(request())["session_id"]
        with pytest.raises(CouncilError) as exc:
            council.submit(request())
        assert exc.value.code == "busy"
        settings.max_prompt_chars = 1000
        assert (await council.wait(sid))["error"]["code"] == "context_limit"
    finally:
        await council.close()


async def test_session_deadline(settings):
    settings.session_timeout_seconds = 1
    class Slow(MockProvider):
        async def invoke(self, prompt):
            await asyncio.sleep(30)
    council = Council(settings, providers={n: Slow(n, c, settings) for n, c in settings.providers.items()})
    try:
        sid = council.submit(request())["session_id"]
        assert (await council.wait(sid))["error"]["code"] == "session_timeout"
    finally:
        await council.close()


def test_single_writer_readers_and_schema(settings):
    writer = Store(settings.database)
    try:
        with pytest.raises(CouncilError) as exc:
            Store(settings.database)
        assert exc.value.code == "database_busy"
        reader = Store(settings.database, read_only=True)
        assert reader.list_sessions() == []
        reader.close()
    finally:
        writer.close()
    db = sqlite3.connect(settings.database)
    db.execute("PRAGMA user_version=99")
    db.close()
    with pytest.raises(CouncilError) as exc:
        Store(settings.database)
    assert exc.value.code == "schema_version"


def test_crash_recovery_and_conservative_count(settings):
    store = Store(settings.database)
    req = request()
    sid = store.create(req, {"alpha": "A", "beta": "B", "gamma": "C"}, {})
    store.reserve(sid, "alpha", "A", "position", 0, "p", 32, 100)
    store.close()
    recovered = Store(settings.database)
    try:
        assert recovered.status(sid)["status"] == "interrupted"
        assert recovered.attempts(sid)[0]["status"] == "interrupted"
        assert recovered.status(sid)["attempts"] == 1
    finally:
        recovered.close()


def test_explicit_config_and_recursive_guard(tmp_path, monkeypatch):
    path = tmp_path / "settings.toml"
    path.write_text('database = "data/council.sqlite3"\n[providers.alpha]\nkind="mock"\n')
    settings = load_settings(path)
    assert settings.database == tmp_path / "data/council.sqlite3"
    monkeypatch.setenv("AI_COUNCIL_WORKER", "1")
    with pytest.raises(CouncilError) as exc:
        Council(settings)
    assert exc.value.code == "recursive_call"


async def test_billing_consent_and_google_opt_in(settings):
    settings.providers["alpha"] = ProviderConfig(kind="codex")
    council = Council(settings)
    try:
        with pytest.raises(CouncilError) as exc:
            council.submit(request())
        assert exc.value.code == "billing_consent"
        settings.providers["alpha"] = ProviderConfig(kind="antigravity", subscription_confirmed=True)
        with pytest.raises(CouncilError) as exc:
            council.submit(request())
        assert exc.value.code == "tool_policy"
    finally:
        await council.close()


async def test_resume_rejects_active_worker(settings):
    council = Council(settings)
    try:
        sid = council.submit(request())["session_id"]
        with pytest.raises(CouncilError) as exc:
            council.resume(sid, accept_duplicate_cost=True)
        assert exc.value.code == "still_running"
        await council.wait(sid)
    finally:
        await council.close()


def test_missing_readonly_database_is_diagnostic(tmp_path):
    with pytest.raises(CouncilError) as exc:
        Store(tmp_path / "missing.sqlite3", read_only=True)
    assert exc.value.code == "database"
