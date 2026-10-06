---
name: uh-ponytail
description: Evaluate a proposed abstraction, dependency, wrapper, or framework against a simpler safe implementation.
---

# Ponytail-inspired minimalism

Local adaptation; no upstream modes, hooks, or performance promises are included.
Use after understanding the existing flow, not instead of reading it.

Ask whether the proposed component is required by today's accepted behavior.
Compare reuse of current code, configuration, a standard library, a native
platform feature, and an existing dependency before introducing a new layer.
A new dependency or abstraction is valid when it reduces actual complexity or
risk; neither one-line code nor a rigid two-call-site rule is the goal.

Offer a concrete smaller alternative and explain what it preserves. Keep trust
boundary validation, accessibility, transactionality, meaningful error handling,
observability, and recovery. A short unsafe workaround is not simplification.
Do not remove unknown code, approved edge cases, or tests on aesthetic grounds.
End with the implementation choice, not a mandatory debt ledger or audit package.
