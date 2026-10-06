# Security policy and deployment boundary

AI-Council v1 is a **single-user, local stdio service**, not a multi-tenant hosted gateway. Do not expose it directly to a public tunnel or attach it to untrusted clients. An authorized MCP client can submit paid/limited provider work, view completed local discussions, and cancel jobs.

## What is enforced

MCP callers cannot choose commands or executable paths. Trusted local TOML config selects installed CLIs. Subprocesses run without a shell, with bounded stdout+stderr, stdin prompts, temporary working directories, deadlines, and process-group cleanup. Common API/cloud/OAuth environment variables are not inherited. The server never reads/copies official credential files. Codex/Claude authentication is checked through their CLI status commands; API-key auth is refused. No API fallback, automatic login, CAPTCHA bypass, credential refresh implementation, or quota evasion is included.

Responses must match a stage schema and known peer/source IDs. Tool execution is disabled through verified client controls where available. Antigravity requires explicit opt-in because it inherits local permissions. Output-event rejection is not a rollback or substitute for OS isolation.

## What is not enforced

Official CLIs still have the current OS user's privileges, HOME, keyring, managed policy, and cached login. This is not a hardened sandbox. Use a separate OS user or suitable sandbox for confidential/high-risk workloads. Read-only filesystems can still reveal secrets. A subprocess that escapes its process group is outside the supervisor guarantee.

Question/context/source content is sent to **each selected provider** and stored locally in a **plaintext** SQLite database with 0600 permissions. Provider privacy/retention settings apply independently. The SQLite database, WAL, reports, and provider-owned histories may contain sensitive content. No raw stderr/auth tokens are intentionally journaled, but a model can reproduce secret input in its answer. Review reports before sharing. To erase local records, stop the server and delete the configured database and its associated `-wal`, `-shm`, and `.lock` files. Provider-side data needs separate deletion.

The user must review subscription eligibility and extra-credit billing. Environment filtering alone cannot disable account-level paid overage. Attempt budgets are not monetary caps and are scoped to a local DB/provider ID. The software must not be used to resell or pool credentials contrary to provider terms.

## Reporting

Do not post credentials, private transcripts, database files, or exploit details in a public issue. Use GitHub private vulnerability reporting when enabled. If it is not enabled, open a minimal issue asking the maintainer for a private reporting channel without sensitive details. Include the version and affected component once a private channel is available.
