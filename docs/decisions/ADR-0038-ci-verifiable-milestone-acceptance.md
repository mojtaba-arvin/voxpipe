# ADR-0038: CI-verifiable repository milestone acceptance

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

VoxPipe is developed and validated through its public GitHub repository. Milestones must not depend on the maintainer provisioning independent hardware, running commands manually, supplying production credentials, or operating a separate staging environment. Such dependencies turn repository work into indefinite external blockers and are not reliably reproducible by contributors or agents.

## Decision

1. Repository milestone acceptance must be executable and repeatable in GitHub Actions using disposable services, deterministic fixtures, fault injection and explicit assertions.
2. Replace hardware-specific performance benchmarks with correctness, concurrency, bounded-load and regression tests on GitHub-hosted runners. A benchmark harness may remain as optional diagnostic tooling, but its output is not a milestone gate and must not be represented as target capacity evidence.
3. Replace host-level systemd drills with process-level signal, graceful-stop, lease-reclaim and restart/reconstruction tests. Validate service-unit syntax statically when useful; do not claim that this proves a production host supervisor.
4. Replace staging telemetry checks with disposable Collector/Prometheus integration tests that validate configuration, synthetic metric delivery, scrape visibility, alert evaluation and outage isolation. Live notification delivery and real staging SLOs are deployment concerns, not repository milestone gates.
5. Replace production canary/rollback exercises with isolated CI deployment-state simulations that prove health-gate rejection, rollback transitions and durable-data/schema safety. Do not claim production rollout validation.
6. Use disposable PostgreSQL backup/restore tests for repository acceptance. Production encryption, off-host retention, PITR and RPO/RTO remain deployment requirements and are not implied by CI.
7. If a requirement has no honest CI equivalent, remove it from the repository milestone acceptance criteria and document it as an explicit deployment limitation rather than leaving the milestone blocked or fabricating evidence.
8. Keep paid provider calls, external data egress and production credentials disabled unless separately authorized. CI must use fakes/local fixtures for provider failure and reconciliation behavior.

## Consequences

Milestones become independently reproducible and can be completed without user-operated infrastructure. GitHub-hosted runner measurements are not hardware capacity claims. CI simulations prove the modeled control path, not external production behavior. Deployment-specific operations may be documented separately but must not block repository milestone completion.

## Applied scope

- **VP-0034:** accepted as complete using existing PostgreSQL recovery, replay-safety, concurrency, graceful-stop and backup/restore CI evidence; target-host throughput and systemd drills are non-gating.
- **VP-0035:** requires disposable telemetry-stack integration in CI, not a live staging environment or notification route.
- **VP-0036:** requires artifact attestation workflow execution and isolated canary/rollback simulation in CI, not a production deployment.
- **VP-0037:** requires protected-branch review/check enforcement and safe-stop/rollback tests, not an autonomous agent or production approval service.


## Additional milestone closures (2026-11-25)

- **VP-0032:** closed using strict capability metadata and tests for explicit remote data egress, data/deployment declarations, deprecation, replacement and rollback metadata. No provider capability is populated from guesses; operators supply verified records.
- **VP-0033:** closed for repository implementation using allowlisted evidence-based routing, process/PostgreSQL breaker behavior, fail-closed coordination, crash-recoverable probe leases, fake-transport ambiguous-timeout tests and replay-safe workflow persistence. Live paid provider activation remains disabled and is not a repository gate.
- **VP-0035:** closed using a disposable Collector/Prometheus GitHub Actions stack, real OTLP SDK metric emission, Prometheus scrape verification and deterministic promtool alert-firing tests. Live notification delivery and staging SLOs remain deployment concerns.
