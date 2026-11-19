# Batch Ingestion Application Flow

## Boundary

`IngestionService` is an application-layer orchestrator, not an HTTP handler or worker queue. It accepts:

- Tenant identity supplied by authenticated application context.
- An idempotency key.
- A server-staged file path beneath a private staging root.
- A small allowlisted source metadata object.
- A retention policy identifier selected by trusted application configuration, never by an untrusted client.

It never accepts a client URL or arbitrary filesystem path. The staging root must be private to the service.

## Processing sequence

```text
staged file
    |
    v
path + tenant + request validation
    |
    v
SHA-256 fingerprint / idempotency lookup
    |
    v
claim expiring worker lease
    |
    v
created -> receiving -> finalizing
    |
    v
ffprobe metadata inspection
    |
    v
full FFmpeg audio decode validation
    |
    v
queued -> processing
    |
    v
atomic object storage put (expected SHA-256)
    |
    v
AudioAsset metadata commit
    |
    v
session -> completed
```

The object store verifies that the bytes it stores match the digest computed before probing. This prevents a changed staging file from being stored after it was inspected. The staging directory must still be service-owned and immutable to untrusted principals.

## Idempotency and tenancy

- The repository enforces a unique `(tenant_id, idempotency_key)` constraint.
- A repeated request with the same tenant, key, content digest, source metadata, and retention policy returns the completed result without reprocessing.
- Active sessions carry an expiring worker lease; a second worker cannot claim a live lease.
- The session stores only a relative private staging reference, never an absolute filesystem path.
- Reusing a key for different content or metadata fails with `INGESTION_IDEMPOTENCY_CONFLICT`.
- The same key in a different tenant is independent.
- Every repository lookup/update includes tenant scope.
- State updates use compare-and-swap against the expected prior state.

## Failure handling

- Media, decode, storage, and repository errors map to stable error codes.
- A failed operation attempts to remove any newly stored object and asset metadata.
- Cleanup failure is surfaced as `INGESTION_CLEANUP_FAILED`; the orphan reconciler retries cleanup for artifacts associated with failed or expired sessions.
- A completed session is returned only after the asset metadata commit succeeds.
- The legacy session recovery worker handles non-queued sessions. Sessions with durable jobs are exclusively recovered by the PostgreSQL queue worker, using the same asset ID and object key.
- Asset timestamps are derived from the session creation time so a restart after object/asset persistence can safely replay idempotent writes.
- The orphan reconciler cleans artifacts associated with failed sessions and returns a structured report; it does not perform broad object-store prefix deletion.
- Raw parser stderr, local paths, and audio bytes are not included in session failure details.

## Persistence adapter status

`SQLiteIngestionRepository` is a durable local/single-node adapter. It uses WAL, full synchronous commits, a unique idempotency constraint, tenant-scoped queries, and compare-and-swap state updates. `PostgresIngestionRepository` is the multi-node metadata adapter with versioned migrations and forced RLS. The authenticated Go API and durable queue use PostgreSQL directly; Python workers use the same session/job contracts.

## Deliberate limitations

- `IngestionService` remains a synchronous application operation, but the durable PostgreSQL queue and lease-renewing worker execute it asynchronously from the API.
- An identical request arriving while a session has a live lease receives `INGESTION_IN_PROGRESS`; it does not start a second worker.
- Lease heartbeats are refreshed at state transitions. The lease TTL must exceed the longest blocking operation; a remote object-store adapter will need periodic heartbeats during long uploads.
- The reconciler is scoped to failed/expired-session artifacts; global object-store orphan discovery requires a future list-capable storage adapter and inventory policy.
- The service does not delete the staged source file; staging cleanup belongs to the upload boundary and retention workflow.
- The local storage adapter is suitable for development/single-node use only.
