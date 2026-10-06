---
name: uh-integration
description: Establish or debug an end-to-end path crossing service, database, proxy, deployment-image, device, or other real runtime boundaries.
---

# Vertical integration first

Write the smallest useful journey: real input -> boundary -> observable result.
Keep its parent outcome open until it is proved or a specific blocked boundary
is reported. Source-ready is a legitimate intermediate status, not runtime success.

Use isolated data and an authorized environment. Start one repeatable slice
before building a generic feed, scheduler, credential platform, or orchestration
framework. Pick the slice based on representative risk; do not require a read
first when the requested problem is a write. Use actual service adapters and
packaged images where those are in scope. Verify proxy/TLS/config as applicable.

Record provider/consumer revisions and fixture identity safely in the existing
test output. Assert the useful response or durable state; 401 only proves a
rejection boundary. Repeat with appropriate idempotency and exercise response
loss/recovery/concurrency where the operation requires them. Do not duplicate a
possibly completed live write to get a clearer receipt.

Fast tests may use fakes, but label them. A third runner can exercise the full
network path; where it runs is less important than which real boundaries it covers.
If the real environment or authority is unavailable, complete safe local tests,
state exactly what remains unverified, and do not grant yourself activation rights.
Reusable infrastructure is not proof of every later integration's semantics.
