# Worker heartbeats and operations semantics

## Lifecycle

Each worker process has a unique UUID and an explicit tenant allowlist. For every authorized tenant, it registers one row in `ingestion_worker_heartbeats`, refreshes `heartbeat_at` every 15 seconds from a dedicated supervisor thread, and marks the row `stopped` on graceful shutdown.

The heartbeat thread is independent of the job execution path and the per-job lease heartbeat. A long-running STT or structuring operation therefore does not make a healthy process appear dead. The job runner separately sets the tenant row to `busy` after claiming work and returns it to `idle` after the execution attempt. The row records the latest job-start and job-completion timestamps.

A hard process crash cannot mark itself stopped. Its last `idle` or `busy` state remains and is classified as stale once the heartbeat is older than 45 seconds. Rows older than seven days are pruned when a worker registers again.

## Tenant isolation

The heartbeat table uses forced row-level security and the same transaction-local `voxpipe.tenant_id` context as the ingestion queue. Every read and write is scoped to one tenant. The worker identifier is never included in API responses or public metrics.

The operations summary exposes only:
- `active` and `stale` process counts
- active `idle` and `busy` counts
- `stale_busy` count
- `oldest_busy_seconds`
- the heartbeat and stale thresholds

The summary remains protected by the dedicated `operations:read` scope. A missing heartbeat table or database failure fails the operations summary closed; no synthetic worker health is returned.

## Interpretation

- `idle` + fresh heartbeat: worker process is alive and currently not executing a job for that tenant.
- `busy` + fresh heartbeat: worker process is alive and has a claimed job; this does not prove semantic progress inside the model.
- stale `idle`: process likely exited or cannot reach PostgreSQL.
- stale `busy`: process may have died during execution; reconcile the durable job lease before any retry or manual action.
- `stopped`: graceful shutdown was recorded and is not counted as active or stale.

Do not replay or terminate a job based only on `oldest_busy_seconds`. Use the existing lease-fencing and retry protocol. Automated paging is not configured by this milestone; the aggregate is available to operator tooling and future alert exporters.
