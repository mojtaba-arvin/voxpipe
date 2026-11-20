---
name: voxpipe-contract-evolution
description: Use when changing VoxPipe OpenAPI, JSON Schema, ingestion/session/job payloads, database JSON contracts, or the Go/Python boundary.
---

# VoxPipe contract evolution

1. Identify the canonical contract and every consumer/producer before editing. Search both Go and Python implementations, SQL, fixtures, docs, and tests.
2. Decide whether the change is additive and backward-compatible. If not, version the public contract or document a migration/compatibility window.
3. Keep tenant identity, storage paths, object keys, retention selection, and other server-authoritative fields out of untrusted client input.
4. Update OpenAPI, JSON Schema, examples, Go request/response handling, Python payload parsing, SQL persistence, and contract tests as applicable.
5. Define stable error codes, retryability, idempotency behavior, and state-transition semantics. Do not make clients infer behavior from prose or incidental status fields.
6. Test old and new valid fixtures, invalid inputs, unknown fields, boundary values, replay behavior, and cross-runtime round trips.
7. Update architecture and operations docs only after verifying the actual implementation. Search for stale statements elsewhere in the docs and README.
8. If a contract is shared through PostgreSQL JSON, add an integration test that writes in one runtime and reads in the other.
9. Keep generated artifacts reproducible and fail CI on drift rather than rewriting them silently.
10. Record compatibility decisions and rollout/rollback steps in the PR and work ledger.
