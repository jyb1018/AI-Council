---
name: uh-tdd
description: Use red-green-refactor for deterministic behavior or a reproducible regression when the expected contract is stable.
---

# Focused TDD

Selective TDD, not a mandatory workflow for every artifact.

Choose an observable case tied to the accepted requirement. Run it before the fix
and establish that it fails for the intended reason, not a missing runtime or
fixture. Make the smallest correct implementation pass it, then refactor without
changing behavior and run the relevant broader checks.

For existing code, characterize behavior where necessary. Tests may change when
a requirement changes or their expectation is demonstrably wrong; explain the
contract correction and preserve or replace its meaningful coverage. Never swap
assertions, skip tests, or mock away the changed boundary merely for green status.

Use properties/invariants for algorithms, idempotency and authorization where
useful. Mock the dependencies outside the test's intended scope, not the exact
integration being claimed. Code coverage and test counts are secondary signals.
For visual UX, exploratory work, infra bring-up, or stochastic ML quality, choose
an appropriate demo/probe/evaluation rather than pretending unit TDD proves it.
Do not fabricate a red phase after implementation or delete valid code to stage one.
