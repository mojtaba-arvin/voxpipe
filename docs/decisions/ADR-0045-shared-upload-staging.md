# ADR-0045: Shared object-based upload staging

- **Status:** Proposed
- **Date:** 2026-11-25
- **Decision owners:** VoxPipe architecture

## Context

VP-0044 moved persistent audio objects behind an optional S3-compatible `ObjectStoragePort`. The Go upload API still writes request bodies under `VOXPIPE_STAGING_ROOT`, and the Python ingestion worker reads those same local paths. API and worker processes therefore still require a shared POSIX volume even when persistent audio objects use S3-compatible storage.

VP-0045 removes this remaining shared-volume dependency without changing the guided local quickstart.

## Proposed decision

### Cross-language staging contract

- Use the same S3-compatible bucket and encryption/checksum configuration as VP-0044 when `VOXPIPE_STORAGE_BACKEND=s3_compatible`; this single backend selection applies to both staging and durable audio objects in the first version.
- Reserve the `staging/v1/` key namespace for transient uploads. The server derives keys as `staging/v1/{tenant_uuid}/{session_uuid}/{upload_uuid}`; tenant identity comes only from authenticated request context.
- Persist an explicit `staging_backend` and opaque `staging_reference` in the durable ingestion-session payload; the job continues to reference its session ID. Keep local references relative to `VOXPIPE_STAGING_ROOT`; never interpret an S3 key as a filesystem path.
- Define a versioned contract for tenant, session, upload, content length, SHA-256, object key, and lifecycle state. Go and Python implementations must share golden contract fixtures.
- Use immutable conditional create, provider-verified SHA-256, required server-side encryption, and bounded retries. Do not expose bucket names, object keys, endpoint URLs, or presigned URLs in public API responses.

### Go API and Python worker behavior

- The Go API streams request bytes into a staging object. It must use the same 512 MiB limit and encryption/checksum requirements as VP-0044. If a request has no trustworthy content length, spool to a private API-local temporary file; never buffer the whole body in memory and never rely on a shared mount in S3 mode.
- The API creates the ingestion job only after the staged object is committed and verified. On database failure, cleanup is idempotent; ambiguous upload outcomes are reconciled by checksum before retry.
- The Python worker claims the job, reads `staging_backend` and `staging_reference` from the tenant-scoped session payload, opens the staging object through the S3-compatible adapter, verifies tenant/key/size/SHA-256 against the session, then writes the durable audio object through `ObjectStoragePort`.
- The durable audio object and its database reference must be committed before the staging object is deleted. Retryable jobs retain staging objects. A crash between object commit and DB commit must be recoverable without losing the source.
- Reconciliation deletes staging objects only when the durable job/session state proves they are no longer live or retryable. Cleanup must be idempotent and race-safe.

### Local compatibility

- `VOXPIPE_STORAGE_BACKEND=local` remains the default. Existing `VOXPIPE_STAGING_ROOT` behavior and the guided local quickstart remain unchanged.
- S3 mode does not require a shared `VOXPIPE_STAGING_ROOT`; each API/worker host may use only private temporary disk for bounded spooling.
- Keep the staging prefix separate from durable asset keys. Apply a dedicated retention policy to abandoned staging objects, but never configure a lifecycle TTL shorter than the maximum supported job retry/retention window.

## Verification

- Fake Go and Python client tests cover contract serialization, tenant key derivation, checksum mismatch, conditional conflicts, interrupted upload, ambiguous commit, retry, and cleanup.
- CI uses a disposable S3-compatible service with no live cloud credentials or external provider.
- The integration test runs Go API and Python worker in separate containers with no shared staging volume; one process uploads and enqueues, the other claims, reads, verifies, persists, and cleans up.
- Tests prove retryable jobs retain source objects and terminal jobs clean them up without deleting objects referenced by live jobs.
- Existing local staging, guided quickstart, tenant RLS, backup/restore and VP-0044 S3 integration tests remain green.

## Consequences and limits

- S3-backed staging removes the shared POSIX volume requirement for API/worker separation; it does not by itself prove production multi-node readiness.
- Production must separately configure least-privilege S3 and KMS permissions, lifecycle/retention, versioning, monitoring, backup/recovery and capacity limits.
- CI proves contract and adapter behavior against a disposable S3-compatible service, not production durability, RPO/RTO, multi-region behavior or customer traffic.
- The local backend remains available for single-node deployments and quickstart.
