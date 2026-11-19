---
name: voxpipe-postgres-reliability
description: Use when changing VoxPipe PostgreSQL schemas, migrations, RLS, repositories, job queues, leases, retries, idempotency, cancellation, or transaction boundaries.
---

# VoxPipe PostgreSQL reliability

1. Read the current migrations, repository adapter, queue implementation, relevant tests, threat model, and PostgreSQL operations guide before editing.
2. Treat tenant isolation as a database invariant: transaction-local tenant context, explicit tenant predicates, forced RLS, non-owner/non-superuser/NOBYPASSRLS application roles, and tenant-scoped foreign keys.
3. Make idempotency explicit for every externally retried operation. Test concurrent duplicates, cross-tenant collisions, and replay with conflicting content.
4. For state transitions, define allowed prior states, transaction scope, lock order, lease ownership, expiry semantics, and stale-worker fencing before writing SQL.
5. Keep a single cross-runtime row-lock order. Go API, Python worker, cancellation, terminal failure, and recovery paths must agree.
6. Use bounded `SKIP LOCKED` claims and bounded recovery batches. Add indexes for actual claim/recovery predicates and justify them.
7. Explicitly type parameters used in polymorphic PostgreSQL expressions when inference is ambiguous, especially JSONB constructors and timestamp/UUID casts.
8. Use migrations that upgrade from the previous supported schema and are safe to re-run through the migration runner. Never edit an already released migration to repair deployed data.
9. Test against real PostgreSQL 16. Include RLS role tests, concurrent transactions, rollback, lock contention, expiry/reclaim, cancellation races, retry exhaustion, and stale-owner rejection.
10. Verify query plans/index usefulness for high-volume paths when performance is part of the claim. Do not invent benchmark conclusions.

A test that passes only as a superuser is not evidence of tenant-safe behavior.
