"""Authenticated loopback UI. One active debate; configuration changes apply to the next run."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request as HTTPRequest
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.middleware.base import BaseHTTPMiddleware

from .capabilities import discover, validate_selection
from .config import Settings
from .council import Council
from .models import Contract, CouncilError, Identifier, Request
from .providers import make_providers


class Choice(Contract):
    model: str | None = None
    effort: str | None = None


class Profile(Contract):
    participants: list[Identifier]
    chair: Identifier | None = None
    rounds: Annotated[int, Field(ge=1, le=3)] = 1
    choices: dict[Identifier, Choice] = Field(default_factory=dict)


class Start(Contract):
    question: Annotated[str, Field(min_length=1, max_length=20000)]
    idempotency_key: str | None = None


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            json.dump(data, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_app(settings: Settings, *, token: str) -> Starlette:
    base = settings.model_copy(deep=True)
    base.max_active_sessions = 1
    profile_path = base.database.with_suffix(".web-profile.json")
    enabled = [name for name, config in base.providers.items() if config.enabled]
    profile = Profile(
        participants=enabled[:6],
        choices={
            name: Choice(model=c.model, effort=c.reasoning_effort) for name, c in base.providers.items()
        },
    )
    if profile_path.exists():
        try:
            profile = Profile.model_validate_json(profile_path.read_text())
        except (ValueError, OSError) as exc:
            raise CouncilError(
                "web_profile", "Saved web profile is invalid; repair or remove it explicitly."
            ) from exc
    catalog: dict[str, dict] = {}
    catalog_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.council = Council(base.model_copy(deep=True))
        try:
            yield
        finally:
            await app.state.council.close()

    async def refresh():
        async with catalog_lock:

            async def one(name, config):
                try:
                    return name, {"models": await discover(config)}
                except CouncilError as exc:
                    return name, {"models": [], "error": exc.as_dict()}

            catalog.update(await asyncio.gather(*(one(n, c) for n, c in base.providers.items())))

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 131072:
                raise CouncilError("input_limit", "Request body exceeds 128 KiB.")
        return json.loads(data)

    def validate_profile(candidate):
        Request(
            question="Validate settings",
            participants=candidate.participants,
            chair=candidate.chair,
            rounds=candidate.rounds,
        )
        for name in candidate.participants:
            config = base.providers.get(name)
            if config is None or not config.enabled:
                raise CouncilError(
                    "provider_disabled", "Enable the participant in the trusted TOML configuration first."
                )
            entry = catalog.get(name, {})
            if entry.get("error") or not entry.get("models"):
                raise CouncilError("capabilities", "Refresh the model catalog successfully before saving.")
            choice = candidate.choices.get(name, Choice())
            validate_selection(entry["models"], choice.model, choice.effort)
        if set(candidate.choices) - set(base.providers):
            raise CouncilError("provider_disabled", "Unknown provider in settings.")

    async def api(request: HTTPRequest):
        nonlocal profile
        council = request.app.state.council
        path = request.url.path
        try:
            if path == "/api/catalog":
                if not catalog or request.method == "POST":
                    await refresh()
                return JSONResponse(catalog)
            if path == "/api/state":
                providers = {
                    name: {
                        "kind": c.kind,
                        "enabled": c.enabled,
                        "subscription_confirmed": c.subscription_confirmed,
                        "allow_inherited_tools": c.allow_inherited_tools,
                    }
                    for name, c in base.providers.items()
                }
                return JSONResponse(
                    {
                        "providers": providers,
                        "profile": profile.model_dump(),
                        "sessions": council.store.list_sessions(),
                        "active": any(not t.done() for t in council.tasks.values()),
                    }
                )
            if path == "/api/profile":
                candidate = Profile.model_validate(await body(request))
                validate_profile(candidate)
                if any(not t.done() for t in council.tasks.values()):
                    raise CouncilError(
                        "busy", "Settings are locked until the current debate finishes or is cancelled."
                    )
                private_json(profile_path, candidate.model_dump())
                profile = candidate
                return JSONResponse(profile.model_dump())
            if path == "/api/requests/cancel":
                data = await body(request)
                key = data.get("idempotency_key") if isinstance(data, dict) else None
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise CouncilError("invalid_input", "A valid idempotency key is required.")
                row = council.store.db.execute(
                    "SELECT id FROM sessions WHERE idempotency_key=?", (key,)
                ).fetchone()
                return JSONResponse(await council.cancel(row[0]) if row else {"status": "not_found"})
            if path == "/api/sessions":
                start = Start.model_validate(await body(request))
                validate_profile(profile)
                if any(not t.done() for t in council.tasks.values()):
                    # Exact duplicate submissions are safe even while active.
                    req = Request(
                        question=start.question,
                        participants=profile.participants,
                        chair=profile.chair,
                        rounds=profile.rounds,
                        idempotency_key=start.idempotency_key,
                    )
                    existing = council.store.existing(req, council._identities(req))
                    if existing:
                        return JSONResponse({**council.store.status(existing), "reused": True})
                    raise CouncilError("busy", "A debate is already active.")
                configured = base.model_copy(deep=True)
                for name in profile.participants:
                    choice = profile.choices.get(name, Choice())
                    configured.providers[name].model = choice.model
                    configured.providers[name].reasoning_effort = choice.effort
                council.settings = configured
                council.providers = make_providers(configured)
                started = council.submit(
                    Request(
                        question=start.question,
                        participants=profile.participants,
                        chair=profile.chair,
                        rounds=profile.rounds,
                        idempotency_key=start.idempotency_key,
                    )
                )
                return JSONResponse(started)
            sid = request.path_params["sid"]
            if path.endswith("/cancel"):
                return JSONResponse(await council.cancel(sid))
            state = council.store.get(sid)
            result = council.store.result(sid)
            # Dedicated human observer. Existing CLI/MCP result remains sealed.
            result.update(
                {
                    "question": state["request"]["question"],
                    "provider_configuration": state["identities"],
                    "alias_to_provider": {a: n for n, a in state["aliases"].items()},
                    "transcript": council.store.attempts(sid),
                }
            )
            return JSONResponse(result)
        except CouncilError as exc:
            return JSONResponse({"error": exc.as_dict()}, status_code=409 if exc.code == "busy" else 400)
        except (ValidationError, ValueError, TypeError):
            return JSONResponse(
                {"error": {"code": "invalid_input", "message": "Check the input and selected settings."}},
                status_code=400,
            )
        except OSError:
            return JSONResponse(
                {"error": {"code": "web_storage", "message": "Cannot persist the web settings."}},
                status_code=500,
            )

    async def asset(request):
        name = request.path_params.get("name", "index.html")
        if name not in ("index.html", "app.js", "style.css"):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(Path(__file__).parent / "static" / name)

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", asset),
            Route("/assets/{name}", asset),
            Route("/api/state", api),
            Route("/api/catalog", api, methods=["GET", "POST"]),
            Route("/api/profile", api, methods=["PUT"]),
            Route("/api/sessions", api, methods=["POST"]),
            Route("/api/requests/cancel", api, methods=["POST"]),
            Route("/api/sessions/{sid}", api),
            Route("/api/sessions/{sid}/cancel", api, methods=["POST"]),
        ],
    )

    async def boundary(request, call_next):
        host = request.headers.get("host", "")
        if host.split(":")[0] != "127.0.0.1":
            return JSONResponse({"error": "invalid_host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin != "http://" + host:
            return JSONResponse({"error": "invalid_origin"}, status_code=403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=boundary)
    return app


async def serve_web(settings: Settings, *, port: int, endpoint: Path, open_browser: bool):
    import uvicorn
    import webbrowser

    token = secrets.token_urlsafe(32)
    app = create_app(settings, token=token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        private_json(endpoint, {"url": url, "token": token})
        print(f"AI-Council Web: {url}/#{token}", file=sys.stderr)
        if open_browser:
            webbrowser.open(f"{url}/#{token}")
        await uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False)).serve(sockets=[sock])
    finally:
        sock.close()
        # Never delete a descriptor replaced by a newer server.
        if endpoint.exists() and json.loads(endpoint.read_text()).get("token") == token:
            endpoint.unlink()
