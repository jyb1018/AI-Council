"""SQLite journal with one writer, durable attempts, and idempotent session creation."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import CouncilError, Request


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path.expanduser().resolve()
        self._lock = None
        self.db = None
        try:
            if read_only:
                self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
            else:
                if os.name != "posix":
                    raise CouncilError("platform", "Use macOS, Linux, or WSL for v1.")
                import fcntl

                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._lock = open(str(self.path) + ".lock", "a+b")
                os.chmod(str(self.path) + ".lock", 0o600)
                try:
                    fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise CouncilError("database_busy", "Another Council writer owns this database. Use a separate database per server.") from exc
                self.db = sqlite3.connect(self.path, timeout=5)
                os.chmod(self.path, 0o600)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA foreign_keys=ON")
            if not read_only:
                self._initialize()
        except BaseException:
            self.close()
            raise

    def _initialize(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise CouncilError("schema_version", "Database was written by an unsupported Council version.")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, fingerprint TEXT NOT NULL,
                request TEXT NOT NULL, aliases TEXT NOT NULL, identities TEXT NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, error TEXT, result TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
                step_key TEXT NOT NULL, provider TEXT NOT NULL, alias TEXT NOT NULL,
                phase TEXT NOT NULL, round INTEGER NOT NULL, status TEXT NOT NULL,
                prompt_hash TEXT NOT NULL, payload TEXT, usage TEXT, reported_model TEXT,
                error TEXT, elapsed_ms INTEGER, started_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS invocation_step ON invocations(session_id, step_key, status);
            CREATE INDEX IF NOT EXISTS invocation_daily ON invocations(provider, started_at);
            PRAGMA user_version=1;
        """)
        with self.db:
            self.db.execute("UPDATE sessions SET status='interrupted', updated_at=? WHERE status IN ('queued','running')", (now(),))
            self.db.execute("UPDATE invocations SET status='interrupted', finished_at=? WHERE status='running'", (now(),))

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def get(self, session_id: str) -> dict:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise CouncilError("not_found", "Council session does not exist.")
        result = dict(row)
        for key in ("request", "aliases", "identities", "error", "result"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result

    def existing(self, request: Request, identities: dict) -> str | None:
        if request.idempotency_key is None:
            return None
        row = self.db.execute("SELECT id,fingerprint FROM sessions WHERE idempotency_key=?", (request.idempotency_key,)).fetchone()
        if row is None:
            return None
        fingerprint = digest({"request": request.model_dump(exclude={"idempotency_key"}), "identities": identities})
        if row["fingerprint"] != fingerprint:
            raise CouncilError("idempotency_conflict", "This idempotency key belongs to a different request or provider configuration.")
        return row["id"]

    def create(self, request: Request, aliases: dict, identities: dict) -> str:
        session_id = uuid.uuid4().hex
        fingerprint = digest({"request": request.model_dump(exclude={"idempotency_key"}), "identities": identities})
        stamp = now()
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,'queued','queued',NULL,NULL,?,?)",
                            (session_id, request.idempotency_key, fingerprint, encode(request.model_dump()),
                             encode(aliases), encode(identities), stamp, stamp))
        return session_id

    def update(self, session_id: str, *, status: str, phase: str,
               error: dict | None = None, result: dict | None = None) -> None:
        with self.db:
            self.db.execute("UPDATE sessions SET status=?,phase=?,error=?,result=?,updated_at=? WHERE id=?",
                            (status, phase, encode(error) if error else None, encode(result) if result is not None else None,
                             now(), session_id))

    def successful(self, session_id: str, step_key: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM invocations WHERE session_id=? AND step_key=? AND status='succeeded' ORDER BY id DESC LIMIT 1",
                              (session_id, step_key)).fetchone()
        return json.loads(row[0]) if row else None

    def reserve(self, session_id: str, provider: str, alias: str, phase: str, round_number: int,
                prompt: str, max_session: int, max_daily: int) -> int:
        stamp = now()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            count = self.db.execute("SELECT COUNT(*) FROM invocations WHERE session_id=?", (session_id,)).fetchone()[0]
            if count >= max_session:
                raise CouncilError("session_budget", "Session attempt budget exhausted. No additional worker was started.")
            daily = self.db.execute("SELECT COUNT(*) FROM invocations WHERE provider=? AND started_at>=?",
                                    (provider, stamp[:10])).fetchone()[0]
            if daily >= max_daily:
                raise CouncilError("daily_budget", "Configured provider's UTC daily attempt budget exhausted.")
            cursor = self.db.execute("""INSERT INTO invocations
                (session_id,step_key,provider,alias,phase,round,status,prompt_hash,started_at)
                VALUES (?,?,?,?,?,?,'running',?,?)""",
                (session_id, f"{phase}:{round_number}:{alias}", provider, alias, phase, round_number,
                 hashlib.sha256(prompt.encode("utf-8")).hexdigest(), stamp))
            return cursor.lastrowid

    def finish(self, invocation_id: int, *, status: str, payload: dict | None = None,
               usage: dict | None = None, reported_model: str | None = None,
               error: dict | None = None, elapsed_ms: int = 0) -> None:
        with self.db:
            self.db.execute("""UPDATE invocations SET status=?,payload=?,usage=?,reported_model=?,error=?,
                elapsed_ms=?,finished_at=? WHERE id=?""",
                (status, encode(payload) if payload is not None else None, encode(usage) if usage else None,
                 reported_model, encode(error) if error else None, elapsed_ms, now(), invocation_id))

    def attempts(self, session_id: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM invocations WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            for key in ("payload", "usage", "error"):
                entry[key] = json.loads(entry[key]) if entry[key] else None
            result.append(entry)
        return result

    def status(self, session_id: str) -> dict:
        session = self.get(session_id)
        attempts = self.attempts(session_id)
        return {"session_id": session_id, "status": session["status"], "phase": session["phase"],
                "participants": session["request"]["participants"], "mode": session["request"]["mode"],
                "planned_calls": Request.model_validate(session["request"]).planned_calls,
                "attempts": len(attempts), "successful_steps": sum(a["status"] == "succeeded" for a in attempts),
                "error": session["error"], "created_at": session["created_at"], "updated_at": session["updated_at"],
                "failures": [{"provider": a["provider"], "phase": a["phase"], "error": a["error"]}
                             for a in attempts if a["status"] in ("failed", "interrupted", "cancelled")]}

    def result(self, session_id: str, *, transcript: bool = False) -> dict:
        session = self.get(session_id)
        result = self.status(session_id)
        result["result"] = session["result"]
        result["simulated"] = any(i["kind"] == "mock" for i in session["identities"].values())
        # No transcript or alias map leaves this API until the entire deliberation completes.
        if session["status"] == "completed":
            result["alias_to_provider"] = {alias: name for name, alias in session["aliases"].items()}
            result["provider_configuration"] = session["identities"]
            result["usage"] = [{"provider": a["provider"], "phase": a["phase"], "round": a["round"],
                                "tokens": a["usage"], "elapsed_ms": a["elapsed_ms"],
                                "reported_model": a["reported_model"]} for a in self.attempts(session_id)]
            if transcript:
                result["transcript"] = self.attempts(session_id)
        return result

    def list_sessions(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(limit, 100))
        ids = self.db.execute("SELECT id FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.status(row[0]) for row in ids]
