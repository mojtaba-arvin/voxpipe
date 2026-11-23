# ADR-0034: PostgreSQL-native durable workflow step journal

- **Status:** Provisional PoC decision; production engine selection deferred pending load and recovery measurements.
- **Date:** 2026-11-22

## Context

VoxPipe already depends on PostgreSQL 16 and has a tenant-scoped durable ingestion queue with lease fencing, retry limits and restart recovery. The next need is multi-step orchestration with persisted step state, approvals, cancellation and replay safety. Introducing a separate orchestration service before measuring the current workload would add operational cost and another data plane.

## Decision

Build a bounded PostgreSQL-native workflow journal as the first proof of concept. It complements rather than replaces `ingestion_jobs`. The PoC uses separate tenant-RLS run/step tables, one active step per workflow, ordered execution, bounded retries, lease recovery, approval gates and idempotent creation. Inputs/results are references and bounded metadata only.

## Alternatives

- **Temporal:** stronger mature workflow history and cross-service orchestration, but adds a separately operated service and workflow versioning requirements.
- **DBOS:** PostgreSQL-backed durable execution with a smaller operational footprint; remains a candidate if the custom journal needs richer scheduling/step semantics.
- **Restate:** useful durable service interactions, but the upstream BSL license and distribution model require separate review.

## Consequences

Positive: reuses PostgreSQL, existing RLS and current operational skills; avoids a new runtime dependency; supports auditable state transitions and human approval.

Negative: custom engine ownership, limited parallelism, no built-in workflow history UI, scheduler or distributed tracing. At-least-once step execution requires idempotent external side effects.

## Exit criteria before production selection

1. Integrate a supervised scheduler/worker pool and prove restart recovery under concurrent claims.
2. Run a reproducible workload comparison against DBOS and Temporal for recovery correctness, duplicate side effects, throughput, p95 latency and operational footprint.
3. Exercise backup/restore and schema upgrades with active workflows.
4. Review security, retention, audit and tenant-isolation behavior under failure injection.
5. Revisit this ADR with measured evidence; do not mark VP-0034 done until these criteria pass.
