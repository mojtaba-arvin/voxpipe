# ADR-0034: PostgreSQL-native durable workflow step journal

- **Status:** Accepted for the repository scope; PostgreSQL-native workflow journal selected. Deployment-scale capacity and production operational validation are explicitly outside this repository milestone.
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

## Repository acceptance evidence

GitHub Actions now proves cross-instance lease expiry/reclaim and stale-worker fencing, replay-safe transcript reuse, 16 concurrent unique claims, repeated 8-worker contention across 64 workflows, graceful worker stop, and disposable PostgreSQL backup/restore with forced-RLS tenant visibility. The existing architecture comparison of Temporal, DBOS and Restate remains the alternatives analysis; a hardware-dependent throughput shootout is not required for this repository decision.

## Explicitly outside repository scope

Target-host throughput/capacity, production-scale restore objectives, host-level systemd restart/shutdown, and operational RPO/RTO depend on deployment hardware, data volume and infrastructure. They are not acceptance criteria for VP-0034 and must not be requested from the maintainer as a prerequisite to closing the repository milestone. The benchmark scripts remain optional diagnostics.
