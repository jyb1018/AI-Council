---
name: uh-tooling
description: Select an available capability for code navigation, current docs, browser verification, repository operations or review; diagnose global/local skill collisions and missing runtimes. Use on a concrete tooling need, not every trivial edit. Does not install tools or authorize external effects.
---
# Capability-aware tooling

1. Identify the outcome, changed boundary, actual project and current host tools.
   Keep the existing seven uh-* workflows; this skill adds capabilities, not roles.
2. Consult `.universal-harness/TOOLING.md` and only the relevant capability in
   `.universal-harness/tooling.json`. Prefer the smallest sufficient available
   provider. Do not require Graphify + Serena + browsers + external review in a chain.
3. For setup, collision or availability diagnosis, run the read-only command from
   the project: `python3 .universal-harness/tooling.py doctor .`.
   It inspects bounded SKILL.md metadata and PATH without starting any tools.
   It does not inspect host plugin catalogs, disabled flags, credentials or health.
   An executable or skill file is NOT proof of activation, authorization or success.
4. If useful, request an advisory route with
   `python3 .universal-harness/tooling.py route . --capability symbols`.
   `--available serena` may be added ONLY after observing Serena in this session's
   actual tool catalog. It records a host report, not successful authentication.
   Read the selected global SKILL.md or the current provider help/schema before use.
5. Resolve same-name exposures by exact path with the user/host before invocation.
   Local does not automatically override global; identical text or a symlink does
   not prove the host merges entries. Do not rename, disable or delete user skills
   automatically. Use an unambiguous native alternative while safe work continues.
6. Check source/version/index freshness; keep graph queries bounded and verify
   important relationships against source. Do not create paid indexes, watchers or
   hooks as an incidental step. Missing/stale indexes do not block scoped source reads.
7. Confirm scope and egress before remote queries/review/telemetry. Use disposable
   browser data and verify the origin. Never expose secrets in a query or diagnostic.
   A PR request authorizes its branch/push/PR, not merge, deployment or paid scans.
8. Capture the observable result and current revision. State which fallback was
   used and which acceptance remains NOT RUN/BLOCKED. Self-review is not independent
   review; passing package tests is not real browser/MCP/model verification.
9. Recheck available tools, target and authority after host refresh, compaction or
   resume. Do not persist an old tool list as current truth or replace project
   checkpoints with provider memory. Stop when the requested outcome is met.
