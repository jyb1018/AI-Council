---
name: uh-review
description: Review a requested diff or material security, money, migration, or cross-system write risk using concrete failure evidence.
---

# Risk-based review

Select review depth by actual failure impact, not file count or the presence of
an API, database call, or deployment-related filename. Routine changes can use
self-review; material privilege, privacy, monetary, destructive data, or distributed
write changes warrant an independent reviewer before the risky release.

Give a reviewer the requirement, relevant invariant, exact diff/revision, and
executed checks. Ask them to trace a plausible counterexample independently,
not approve the author's summary. A fresh context helps but does not guarantee
independence; relabeling the same agent is not independent verification.

A useful finding states location, failing scenario, impact, and evidence. Separate
blocking defects, nonblocking observations, and unknowns. Fix demonstrated defects
within scope; do not recursively create reviewer agents or implement every style
suggestion. A changed requirement is a decision, not a hidden code fix.

If independent review is unavailable, perform safe self-review/local validation
and mark the risky release unapproved. Do not freeze unrelated safe development
or claim a reviewer passed. Stop when relevant findings are resolved and the
requested acceptance is established, not when an artifact checklist is full.
