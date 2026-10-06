# Architecture and invariants

## Components

`models.py` defines Pydantic contracts and stage schemas. `prompts.py` builds source-bound, explicitly untrusted input packets. `providers.py` maps official CLI envelopes to validated data. `process.py` owns bounded process groups and environment filtering. `council.py` implements stage barriers and job lifecycle. `store.py` owns durable SQLite state. `server.py` exposes MCP stdio jobs; `cli.py` provides terminal operations.

There is no LLM API router, external message broker, A2A layer, LangGraph dependency, account service, or implicit repository reader. Local configuration is trusted operator input; MCP request fields cannot choose executable paths or arbitrary CLI flags.

## State transitions

```text
queued -> running(position:0)
         -> running(critique:1) -> running(revision:1)
         -> ... additional bounded critique/revision pairs ...
         -> running(synthesis:R) -> completed

any active state -> failed | cancelled | interrupted
failed/cancelled/interrupted -- explicit consent --> queued
```

All participants are required at each stage. Gather completes every submitted worker before a successful barrier opens. One failure seals the stage and prevents the next stage. There is no implicit quorum reduction, majority vote, auto retry, or fallback to a paid API.

First-round prompts contain only the common question/context/sources/language/mode. Alias assignment is random and stable within a session. Cross-critique receives every peer's answer except the reviewer's own. Revision receives the author's answer and all targeted reviews. Synthesis receives anonymous final answers and the critique history. Individual concise rationales are stored; hidden chain-of-thought is not requested.

## Persistence and recovery

New default databases on macOS use `~/Library/Application Support/ai-council/`; Linux/WSL retain `~/.local/share/ai-council/`. On macOS, each existing legacy default file (`council.sqlite3` or `demo.sqlite3`) is selected independently and remains in place. Explicit database paths take precedence. Permission errors during database directory/file setup become `database_permissions` with guidance to select a writable path; existing databases are not relocated or bypassed on failure. The SQLite schema is unchanged.

One database has one POSIX-locked writer. Read-only clients may query alongside it. Tables store immutable request/alias/provider identities, latest session status, and each invocation attempt. SQLite uses WAL and transactions. Invocation reservation precedes process launch, so failed preflight and interrupted calls conservatively consume a local attempt slot. Daily limits are keyed by configured provider ID and reset at UTC midnight; they are not real account quotas.

The idempotency fingerprint binds the complete request (excluding the key itself) and provider kind/executable/model and an explicit reasoning effort (omitted when unset, preserving legacy fingerprints). Reusing a key with changed input is an error. Resume retains the original aliases and reuses succeeded step keys. Changing a provider identity/model/effort requires a new session. No externally running CLI session/thread is reused.

Process startup marks unfinished records interrupted. Resume requires acknowledgement that an external call may have finished/billed before its result was durably committed. This system provides durable step reuse, **not exactly-once external billing**. CLI/MCP result APIs keep payloads sealed until completion. The authenticated Web human observer explicitly exposes validated intermediate payloads and provider aliases; these are never fed back into independent worker prompts. The optional Buzz bridge publishes them to the operator-selected room.

Every call has process output and wall-time bounds. Whole sessions have a separate deadline. POSIX groups are terminated on error/cancellation so ordinary descendants cannot retain pipes indefinitely. A malicious child that deliberately escapes its process group is outside this supervisor's containment guarantee. The worker receives a recursion marker so a Council launched from its inherited environment refuses to start.

## Testing boundaries

Tests cover core state transitions, request contracts, all-peer target validation, sealed drafts, protocol envelopes, real subprocess limits, and a real MCP SDK stdio client against mock providers. Mock tests prove orchestration properties, not official provider compatibility or answer quality. CLI identity checks are capability based; a verified flag set is not a signed version attestation.

No automatic fact-checking is performed. Only membership in the user-supplied source-ID set is enforced. Confidence is a self-report, and a chair's final answer is not a proof. Instructions inside source material are untrusted and tools are restricted where official clients support it, but prompt injection cannot be solved by prompting alone.

## Web and Buzz contracts

`capabilities.py` reads native catalogs without inference. `web.py` serves an authenticated loopback chat/settings UI and owns one Council writer; `buzz.py` is a narrow ACP-to-loopback bridge with fixed-channel official Buzz CLI delivery. Static UI uses DOM textContent, not model-supplied HTML. See `docs/buzz.md` for the operator contract and verification limits.

The Web server accepts one active debate, rejects profile edits while it runs, and fixes model/effort identities before submit. Per-provider model/effort selections are persisted in a 0600 `*.web-profile.json`; immutable per-session identities remain in existing SQLite schema 1. No SQL migration is needed. TOML alone controls executable paths, consent, enablement, tool permissions and budgets.

All `/api/*` endpoints require bearer authentication, loopback Host, and a matching Origin when present. No CORS or public binding is provided. GET `/api/state` returns the current profile and recent sessions; GET/POST `/api/catalog` reads/refreshes capabilities; PUT `/api/profile` saves participants/choices/chair/rounds; POST `/api/sessions` submits question/idempotency_key; GET `/api/sessions/{id}` observes validated intermediate replies; POST `/api/sessions/{id}/cancel` cancels; POST `/api/requests/cancel` cancels by idempotency_key when a submit response is lost. Bodies are limited to 128 KiB. No model execution occurs during catalog discovery.

Buzz supports initialize, session/new, session/prompt and session/cancel over NDJSON stdio. Only a single current framed `/council` event from the explicitly configured channel is accepted. Its event ID binds idempotency. Same-channel turns are serialized with an asyncio lock and POSIX file lock. Delivery receipts are persisted separately; there is no atomic transaction across Buzz publication and local receipt storage. Failure cancels active work where the Web server remains reachable. No automatic retry or recovery hides an ambiguous external delivery.
