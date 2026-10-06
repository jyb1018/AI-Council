import asyncio
import httpx
import pytest
from ai_council.config import demo_settings
from ai_council.web import create_app


@pytest.fixture
async def client(tmp_path):
    app = create_app(demo_settings(tmp_path / "web.sqlite3"), token="test-token")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client:
            yield client


async def test_auth_and_origin(client):
    assert (await client.get("/api/state")).status_code == 401
    client.headers["Authorization"] = "Bearer test-token"
    assert (await client.get("/api/state")).status_code == 200
    assert (await client.get("/api/state", headers={"Origin": "https://evil.example"})).status_code == 403
    assert (await client.get("/api/state", headers={"Host": "evil.example"})).status_code == 403


async def test_observer_and_validation(client):
    client.headers["Authorization"] = "Bearer test-token"
    assert (await client.get("/api/catalog")).status_code == 200
    bad = await client.put(
        "/api/profile",
        json={
            "participants": ["alpha", "beta"],
            "rounds": 1,
            "choices": {"alpha": {"model": "invented", "effort": "ultra"}},
        },
    )
    assert bad.status_code == 400
    profile = {
        "participants": ["alpha", "beta"],
        "rounds": 1,
        "choices": {
            "alpha": {"model": "simulated", "effort": None},
            "beta": {"model": "simulated", "effort": None},
        },
    }
    assert (await client.put("/api/profile", json=profile)).status_code == 200
    result = await client.post(
        "/api/sessions", json={"question": "Test question", "idempotency_key": "web-test"}
    )
    assert result.status_code == 200, result.text
    sid = result.json()["session_id"]
    for _ in range(100):
        snapshot = (await client.get("/api/sessions/" + sid)).json()
        if snapshot["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.01)
    assert snapshot["status"] == "completed"
    assert len(snapshot["transcript"]) == 7
    assert snapshot["provider_configuration"]["alpha"]["model"] == "simulated"
    repeat = await client.post(
        "/api/sessions", json={"question": "Test question", "idempotency_key": "web-test"}
    )
    assert repeat.json()["session_id"] == sid
    assert repeat.json()["reused"]


async def test_live_observer_preserves_blind_barrier_and_locks_settings(tmp_path, monkeypatch):
    from ai_council.providers import MockProvider

    ready = asyncio.Event()
    release = asyncio.Event()
    phases = []

    class Delayed(MockProvider):
        async def invoke(self, prompt):
            phases.append(prompt.phase)
            if self.name == "beta" and prompt.phase == "position":
                ready.set()
                await release.wait()
            return await super().invoke(prompt)

    settings = demo_settings(tmp_path / "observer.sqlite3")

    def providers(settings):
        return {name: Delayed(name, config, settings) for name, config in settings.providers.items()}

    monkeypatch.setattr("ai_council.web.make_providers", providers)
    app = create_app(settings, token="fixture")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": "Bearer fixture"},
        ) as client:
            await client.get("/api/catalog")
            req = {"question": "observer test", "idempotency_key": "observer-test"}
            started = (await client.post("/api/sessions", json=req)).json()
            sid = started["session_id"]
            await asyncio.wait_for(ready.wait(), 3)
            snapshot = (await client.get("/api/sessions/" + sid)).json()
            assert snapshot["status"] == "running"
            assert any(a["payload"] for a in snapshot["transcript"])
            assert set(phases) == {"position"}
            assert "transcript" not in app.state.council.store.result(sid, transcript=True)
            profile = (await client.get("/api/state")).json()["profile"]
            assert (await client.put("/api/profile", json=profile)).status_code == 409
            assert (await client.post("/api/sessions", json=req)).json()["reused"]
            assert (await client.post("/api/sessions", json={"question": "different"})).status_code == 409
            cancelled = await client.post("/api/requests/cancel", json={"idempotency_key": "observer-test"})
            assert cancelled.json()["status"] == "cancelled"
            assert not release.is_set()
            assert set(phases) == {"position"}
