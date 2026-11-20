# Durable Ingestion Job Queue

## Scope

Migration `0003_create_durable_ingestion_jobs.sql` adds a PostgreSQL-backed queue for asynchronous ingestion. It is separate from the ingestion session state machine: the session describes the media lifecycle; the job describes delivery and execution attempts.

## State model

```text
pending --claim--> leased --complete--> completed
   ^                  |
   |                  +--retryable failure, attempts remain--> pending
   |                  |
   |                  +--permanent failure / retry exhaustion--> dead
   |                  |
   +---- expired lease (attempts remain) -----------------------+
                      |
                      +--cancel--> cancelled
```

- Enqueue is idempotent per `(tenant_id, session_id)`.
- A claim increments `attempts` and assigns a UUID worker lease.
- Claims use `FOR UPDATE SKIP LOCKED` inside one PostgreSQL transaction.
- Expired leases are reclaimed; an expired final attempt becomes terminal.
- Heartbeat, completion, and failure require the current owner and an unexpired lease.
- Cross-runtime state transitions lock the session before the job.
- The application role is non-owner, non-superuser, and `NOBYPASSRLS`; every query sets transaction-local tenant context.

## Worker supervision and observability

The runner accepts only an explicit, deduplicated `VOXPIPE_TENANT_IDS` allowlist. It polls each tenant independently and catches transient claim/queue failures at the supervisor boundary. Retry delay grows exponentially, is capped by `VOXPIPE_WORKER_MAX_BACKOFF_SECONDS`, and includes jitter. Database exception strings are not logged because drivers can include connection details.

A worker renews its lease every third of the configured lease duration. A transient heartbeat failure is tolerated only while the last confirmed renewal remains within a 75% safety window; the next retry wait is shortened to the remaining safety margin, so fencing does not wait for a full heartbeat interval. If that window is consumed, or PostgreSQL explicitly reports `JobLeaseLost`, the worker fences all acknowledgements. The job is left for expiry/reclaim rather than being completed or failed by a worker that cannot prove ownership.

SIGINT/SIGTERM stop new claims and polling. An in-flight job is allowed to finish its bounded processing path and is acknowledged only if its lease remains valid. If the process is terminated before completion, PostgreSQL lease expiry makes the job recoverable.

Queue health is exposed through the tenant-scoped `PostgresIngestionJobQueue.metrics(tenant_id)` query and the operator CLI `PYTHONPATH=src python scripts/inspect_ingestion_queue.py --tenant-id <UUID>` (with `VOXPIPE_DATABASE_DSN` configured):
- pending depth and oldest pending age;
- active, unexpired leases;
- retry count and dead-letter count;
- average completed job duration.

The worker also maintains in-process per-tenant outcome counters and aggregate processing duration. These counters reset on process restart; PostgreSQL queue metrics remain durable. No audio, transcript, token, or storage path is included in metrics.

## Operational limits

- The scheduler is responsible for the tenant allowlist; the application role intentionally has no global cross-tenant claim.
- A deployment should scrape or periodically log queue metrics from a trusted operational process. No public unauthenticated metrics endpoint is exposed.
- The local storage adapters still require API and worker processes to share private volumes on one host.
