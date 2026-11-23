# Production SLOs, metrics and alerting

**Status:** Initial operational contract; targets are provisional until measured against a production-like staging workload.

## Metric cardinality and privacy

Public operational metrics must not include tenant IDs, asset IDs, transcript IDs, session IDs, job IDs, speaker IDs, filenames, audio, transcript text, query text, API tokens or raw provider payloads. Use only bounded labels such as `stage`, `outcome`, `provider`, `model_family`, and stable error-code enums. Never use a customer-controlled string as a metric label.

`WorkerMetrics.aggregate_snapshot()` provides a low-cardinality aggregate for worker outcomes, execution count and processing duration. The tenant-partitioned `snapshot()` is privileged diagnostic data and must not be exposed as a public Prometheus endpoint or copied into shared dashboards.

## Candidate service-level objectives

These are starting targets, not measured guarantees. Review after at least 14 days of representative staging traffic.

| SLI | Provisional objective | Measurement |
|---|---:|---|
| Valid upload acceptance availability | 99.5% per rolling 30 days | Successful acceptance responses / valid upload attempts |
| Durable enqueue success | 99.9% per rolling 30 days | Persisted jobs / valid enqueue requests |
| Queue freshness | 95% of pending jobs claimed within 60 seconds | Enqueue-to-first-claim age |
| Processing completion | 99% of eligible jobs reach completed or an explicitly classified terminal state | Terminal eligible jobs / eligible jobs |
| Retention/deletion propagation | 100% within 24 hours of approved expiry/deletion | Source and derived records confirmed deleted by deadline |
| Worker execution visibility | 99.9% of executions emit a terminal outcome metric | Terminally observed executions / claimed executions |

Do not count invalid media, explicit cancellation, customer approval waits or policy denials as infrastructure failures. Keep their outcomes distinct.

## Alert rules

- **Queue age:** page when oldest eligible pending job exceeds 5 minutes for 5 consecutive minutes.
- **Dead letters:** page on any sustained increase in dead-letter jobs; include only bounded error codes.
- **Retry storm:** warn when retry-scheduled outcomes exceed 5% of claims over 15 minutes.
- **Worker liveness:** page when no worker heartbeat is observed for 3 expected polling intervals while eligible queue depth is non-zero.
- **Deletion deadline:** page when any deletion workflow approaches 12 hours without verified completion; escalate at 24 hours.
- **Processing failure:** warn when eligible processing failure ratio breaches the agreed error budget over 15 minutes.

Tune thresholds to measured baseline and paging fatigue; avoid alerting on individual customer identifiers.

## Runbook: queue backlog

1. Check worker liveness, database connectivity and queue age aggregates.
2. Inspect bounded error-code counts and recent deployment changes.
3. Check expired leases and retry exhaustion using tenant-authorized operator tooling.
4. Do not replay a job outside its idempotency and lease-fencing protocol.
5. If the database is degraded, preserve pending jobs; do not clear queue rows to make the backlog appear healthy.
6. Verify recovery by observing claim latency and terminal outcomes return to baseline.

## Remaining implementation

A metrics exporter, tracing backend, alert manager, dashboard, synthetic checks and staging load validation are not yet configured in this repository. Do not claim the SLOs are active until those components and alert tests exist.
