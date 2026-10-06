# Changelog

## Unreleased

- Added an authenticated loopback chat/settings UI with live validated stage replies, cancellation, session history, and per-participant model/effort selection. CLI/MCP result sealing and worker blind barriers remain unchanged. The Web observer is an explicit new human disclosure boundary.
- Added runtime catalogs from Codex model/list, Claude initialization model metadata, and Antigravity models/help, preserving all advertised effort values. Antigravity's CLI-wide capability limitation is visibly distinguished. Explicit reasoning effort is forwarded to the official CLI and bound into session identity; unset effort preserves existing fingerprints and SQLite schema 1.
- Added a Buzz 0.5.26 custom ACP bridge and credential-free harness generator. Fixed-channel /council events publish validated stage replies through the official Buzz CLI, with request idempotency, channel locking, delivery receipts, cancellation and visible failure. Local Web/mock transport and installed CLI catalogs are verified. Following user approval, a real Buzz workspace/channel round trip completed 10 simulated Council calls and 12 published thread replies. The authorized live switch subsequently completed 7 real Codex/Claude calls and 9 thread replies using a separate live database; the existing subscription consent and disabled Antigravity setting were preserved.

- Fixed first-run demo/database creation on macOS when `~/.local/share` is owned by root: new default databases now use `~/Library/Application Support/ai-council/`. Existing legacy default files remain selected independently, with no migration or schema change; Linux/WSL and explicit paths keep their behavior.
- Database setup permission failures now report `database_permissions` instead of the CLI's generic `invalid_input`. Added regression coverage for an unwritable legacy directory, platform defaults, existing-session preservation, and an explicit unwritable path.

## 1.0.0 — 2026-10-06

- Added local official-CLI adapters for Codex, Claude Code, and opt-in Antigravity.
- Added an explicit blind barrier, anonymous all-peer cross-critique, bounded revision rounds, and chair synthesis.
- Added asynchronous MCP stdio jobs, a synchronous command-line interface, labelled offline simulation, and JSON/Markdown export.
- Added SQLite persistence, idempotency keys, conservative attempt budgets, cancellation, crash recovery, and consent-gated resume.
- Added capability/authentication diagnostics, strict response schemas, source-ID validation, and bounded subprocess execution.
- Added automated tests, Python 3.11/3.13 CI, setup examples, and Korean operational documentation.

Live subscription-account compatibility and comparative answer quality require user-side smoke tests. This version does not claim that three models outperform one model or that subscription billing is always zero.
