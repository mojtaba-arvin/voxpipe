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
- Claims use `FOR UPDATE SKIP LOCKED` inside one PostgreSQL transaction. Competing workers cannot receive the same live lease.
- Expired leases are eligible for reclaim. An expired final attempt is moved to `dead` instead of remaining leased forever.
- Heartbeat, completion, and failure require the current owner and an unexpired lease. This fences stale workers after lease expiry/reclaim.
- When a job reaches the dead-letter state, the associated non-terminal session is atomically moved to failed with a terminal failure code. This prevents the separate stale-session recovery worker from resurrecting work that the durable queue has exhausted.
- Retryable service failures retain the session's non-terminal lifecycle state, clear its session lease, and return the job to `pending` only while attempts remain. The next worker reclaims the session and clears the prior failure marker before resuming. Permanent failures and exhausted retries move to `dead`.
- Cancellation updates the job and non-terminal session in one transaction.

## Tenant isolation

The table has forced row-level security and every operation sets transaction-local `voxpipe.tenant_id`. Every query also includes an explicit tenant predicate. The queue intentionally has no global claim method. A trusted scheduler must enumerate authorized tenants outside the application role and invoke a worker per tenant. PostgreSQL recovery scans exclude every session that has a durable job; the queue worker is the sole recovery owner for those sessions. The legacy session recovery worker remains for sessions created through the non-queued local ingestion path.

The application role must be non-owner, non-superuser, and `NOBYPASSRLS`. Grant it the required CRUD privileges on `ingestion_jobs` in addition to the session and asset tables.

## Worker contract

A worker must:

1. Claim one job for an authorized tenant.
2. Start a heartbeat before work that can exceed the lease duration.
3. Process only the referenced session within the same tenant.
4. Complete only while it still owns a live lease.
5. Classify failures; retry only transient failures and always pass a bounded delay.
6. Treat lease loss as loss of authority: do not acknowledge or overwrite newer state.

`IngestionJobWorker` now provides a single-job execution unit. It renews the queue lease on a background heartbeat, delegates media processing to the existing `IngestionService.resume_session`, classifies retryable session conflicts, applies bounded exponential backoff, and refuses to acknowledge a lost lease. The process supervisor/scheduler must invoke it per authorized tenant.

The authenticated upload HTTP surface and streaming staging lifecycle remain the outstanding parts of VP-0015.

## Deliberate constraints

- The queue stores metadata only; audio bytes remain in private staging/object storage.
- The job table does not contain arbitrary payloads or client-controlled storage paths.
- The queue has one job per session. A new processing attempt for the same session is a retry, not a new job.
- There is no cross-tenant administrative query through the app role.
