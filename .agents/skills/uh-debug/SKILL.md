---
name: uh-debug
description: Diagnose an unexplained or repeating test, runtime, CI, or integration failure before speculative changes.
---

# Evidence-led debugging

An independently written selective application of systematic debugging concepts.

Capture the exact failing command/action, environment/revision, first actionable
error, and whether the code was reached. Redact credentials and personal data;
retain useful error types, safe stages, and correlation information.
Trace the first incorrect value or boundary backward. Compare a working path or
baseline, then state one falsifiable explanation and run the smallest experiment
that distinguishes it. Apply a correction supported by the result and rerun the
original reproducer plus the relevant regression boundary.

Syntax/import errors may have a direct obvious cause; do not manufacture a
multi-stage investigation. For nondeterminism, retain the original failure and
compare repeated same-revision runs under known conditions. A later pass does
not erase a failure. A denied tool is not a product failure or permission grant.

Do not add retries, sleeps, wrappers, suppression, broad refactoring, or new
classifier infrastructure without evidence they address the actual cause.
After repeated equivalent failures, change the experiment or surface the blocker;
continue when new evidence justifies progress. Do not enforce an arbitrary total
attempt count, produce mandatory evidence files, or rename a failure to reset it.
