---
name: uh-developer-context-sync
description: Help a human understand an unfamiliar project (ONBOARD), recover context after AI or team changes (CATCH-UP), or inspect one concept (DEEP-DIVE). Explain purpose, decisions, boundaries, invariants and failure paths with source-grounded diagrams. Not an automatic report for every edit or an agent-memory replacement.
---
# Developer context sync

1. Resolve the actual repository, applicable instructions, requested scope and
   available evidence. Read relevant code/tests and existing architecture/ADR docs.
   Prioritize project-owned invariants/approvals; generic defaults cannot waive them.
   Freeze the inspected revision; report staged, unstaged and relevant untracked
   work separately. Do not execute application code, install tools, call models,
   upload source or change product files merely to explain them.
2. Select ONBOARD for first contact, CATCH-UP for changes since a known point, or
   DEEP-DIVE for one question. Proceed with useful evidence rather than a setup
   interview. No Git/history/docs is a supported reduced-evidence ONBOARD case.
3. For CATCH-UP, prefer the user's explicit base, then this reader's explicitly
   acknowledged checkpoint for this project and scope. Validate commit existence,
   project identity and ancestry against the frozen head before diffing. Do not
   silently substitute HEAD~N, a date, another reader, or a merge base. Missing,
   shallow or diverged history must be disclosed; offer a current-state map or
   clearly labelled endpoint comparison without advancing the checkpoint.
4. Read base and head source, relevant tests, decisions and Git changes. Group by
   mental-model impact, not commit/file count. Explain changes to product behavior,
   responsibility, contracts, data/state flow, security, dependencies, invariants,
   failure/recovery and verification. Even fixtures/config/refactors can matter;
   omit only changes verified to have no material semantic impact.
5. Teach concept -> responsibility/decision -> runtime flow -> code entry point.
   ONBOARD covers purpose/non-goals, domain, boundaries, critical flow, invariants,
   important decisions/tradeoffs, failure/recovery and test/operational limits.
   CATCH-UP covers before -> after, why (or unknown), impact, new risks and what the
   human must now know. DEEP-DIVE stays within the requested concept and neighbours.
6. Load `uh-human-diagramming` for relational explanations. Default ONBOARD to
   3-5 focused views (at most 7), CATCH-UP to 1-3 including a change view, and
   DEEP-DIVE to 1-2. Smaller questions need fewer, sometimes none. Do not force
   unknown structure into a diagram or create a permanent architecture platform.
7. Ground important nodes, edges and assertions in revision + path + symbol or
   verified lines/tests/ADR. Separate observed implementation, documented intent,
   inference and proposal. Highlight contradictions; code does not prove intent,
   and docs/graphs do not prove runtime behavior. Never invent a historical ADR,
   fallback, approval rule, benchmark or deployment fact.
8. End with must-know decisions, unknowns and 2-4 optional scenario/teach-back
   prompts; answer gaps on request, not through a mandatory quiz or approval loop.
   Presenting an explanation is not proof of comprehension or code approval.
9. State is optional: a caller-supplied base works without any writes. Only with
   permission, keep a small per-reader, per-project checkpoint outside shared
   tracked docs. Separate presented_revision from acknowledged_revision and scope;
   advance the latter only on explicit acknowledgement of that scope. Preserve
   open questions and existing state; never edit installer STATE.json, infer
   personal proficiency, commit a reader profile or record secrets. No automatic
   hook, watcher, per-commit sync, release or global configuration change.
