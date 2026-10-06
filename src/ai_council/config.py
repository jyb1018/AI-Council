"""Explicit local configuration; no tokens or provider credentials belong here."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .models import Contract, CouncilError, Identifier


def _default_database(filename: str) -> Path:
    legacy = Path.home() / ".local" / "share" / "ai-council" / filename
    if sys.platform != "darwin" or legacy.exists():
        return legacy
    return Path.home() / "Library" / "Application Support" / "ai-council" / filename


DEFAULT_DB = _default_database("council.sqlite3")


class ProviderConfig(Contract):
    kind: Literal["codex", "claude", "antigravity", "mock"]
    enabled: bool = True
    executable: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: Annotated[float, Field(ge=1, le=1800)] = 300
    daily_call_limit: Annotated[int, Field(ge=1, le=10000)] = 100
    subscription_confirmed: bool = False
    allow_inherited_tools: bool = False

    @model_validator(mode="after")
    def validate_strings(self) -> ProviderConfig:
        for value in (self.executable, self.model, self.reasoning_effort):
            if value is not None and (not value.strip() or "\x00" in value or len(value) > 1024):
                raise ValueError("executable/model must be nonempty and contain no NUL")
        return self

    @property
    def binary(self) -> str:
        return self.executable or {"antigravity": "agy"}.get(self.kind, self.kind)

    def identity(self) -> dict:
        identity = {"kind": self.kind, "executable": self.binary, "model": self.model}
        # Preserve fingerprints of v1 sessions which used the CLI default.
        if self.reasoning_effort is not None:
            identity["reasoning_effort"] = self.reasoning_effort
        return identity


class Settings(Contract):
    database: Path = DEFAULT_DB
    max_calls_per_session: Annotated[int, Field(ge=1, le=128)] = 32
    max_concurrency: Annotated[int, Field(ge=1, le=6)] = 3
    max_active_sessions: Annotated[int, Field(ge=1, le=8)] = 2
    session_timeout_seconds: Annotated[float, Field(ge=1, le=14400)] = 1800
    max_prompt_chars: Annotated[int, Field(ge=1000, le=500000)] = 120000
    max_output_bytes: Annotated[int, Field(ge=1024, le=8388608)] = 1048576
    providers: dict[Identifier, ProviderConfig] = Field(default_factory=lambda: {
        "codex": ProviderConfig(kind="codex"),
        "claude": ProviderConfig(kind="claude"),
        "gemini": ProviderConfig(kind="antigravity", enabled=False),
    })


def load_settings(path: str | Path | None = None) -> Settings:
    explicit = path or os.environ.get("AI_COUNCIL_CONFIG")
    if not explicit:
        return Settings()
    location = Path(explicit).expanduser().resolve()
    try:
        settings = Settings.model_validate(tomllib.loads(location.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise CouncilError("config", "Cannot load configuration; check its path and TOML schema.") from exc
    db = settings.database.expanduser()
    settings.database = db if db.is_absolute() else location.parent / db
    return settings


def demo_settings(database: Path | None = None) -> Settings:
    return Settings(
        database=database or _default_database("demo.sqlite3"),
        providers={name: ProviderConfig(kind="mock") for name in ("alpha", "beta", "gamma")},
    )
