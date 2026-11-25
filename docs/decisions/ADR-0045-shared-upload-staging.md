# ADR-0045: Shared object-based upload staging

- **Status:** Accepted
- **Date:** 2026-11-25
- **Decision owners:** VoxPipe architecture

## Context

VP-0044 moved persistent audio objects behind an optional S3-compatible `ObjectStoragePort`. The Go upload API still writes request bodies under `VOXPIPE_STAGING_ROOT`, and the Python ingestion worker reads those same local paths. API and worker processes therefore still require a shared POSIX volume even when persistent audio objects use S3-compatible storage.

VP-0045 removes this remaining shared-volume dependency without changing the guided local quickstart.

## Proposed decision

### Cross-language staging contract

- Use the same S3-compatible bucket and encryption/checksum configuration as VP-0044 when `VOXPIPE_STORAGE_BACKEND=s3_compatible`; this single backend selection applies to both staging and durable audio objects in the first version.
- Reserve the `staging/v1/` key namespace for transient uploads. The server derives one immutable key per session as `staging/v1/{tenant_uuid}/{session_uuid}/source`; tenant identity comes only from authenticated request context. Reusing a session key with different bytes conflicts; retries with identical bytes are idempotent.
- Persist an explicit `staging_backend` and opaque `staging_reference` in the durable ingestion-session payload; the job continues to reference its session ID. Keep local references relative to `VOXPIPE_STAGING_ROOT`; never interpret an S3 key as a filesystem path.
- Define a versioned contract for tenant, session, upload, content length, SHA-256, object key, and lifecycle state. Go and Python implementations must share golden contract fixtures.
- Use immutable conditional create, provider-verified SHA-256, required server-side encryption, and bounded retries. Do not expose bucket names, object keys, endpoint URLs, or presigned URLs in public API responses.

### Go API and Python worker behavior

- The Go API streams request bytes to a private API-local temporary file while calculating size and SHA-256, then uploads that bounded spool to the staging object. It must use the same configured upload limit (512 MiB by default) and encryption/checksum requirements as VP-0044. This avoids buffering the whole request in memory and never relies on a shared mount in S3 mode.
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
- The adapter interoperability test runs Go and Python in independent processes against one disposable Silo bucket without sharing a staging filesystem. PR #156 adds and passes the separate-container deployment E2E: Go API and Python queue worker run in independent containers with no shared staging/object volume; the public API uploads and enqueues a generated WAV, the worker claims and persists it, tenant-scoped reconciliation removes the staging object, and a final check verifies the durable object and SHA-256 remain.
- Tests prove retryable jobs retain source objects and terminal jobs clean them up without deleting objects referenced by live jobs.
- Existing local staging, guided quickstart, tenant RLS, backup/restore and VP-0044 S3 integration tests remain green.

## Implementation and verification

- Core implementation: PR #155, merge commit `ccbe90c5dc68d3f671801a4e07431203ee3a17c6`; exact-head CI run `45624282856` passed all seven jobs, dependency audit run `45624282950` passed both audits, and post-merge main validation run `45624632771` passed all seven jobs.
- Separate-container deployment proof: PR #156, exact-head CI run `45625810038` passed all seven jobs. The S3 job ran the Go API and Python worker in separate containers without shared staging or object filesystem mounts, processed a generated 32,044-byte WAV, reconciled the terminal session from the worker container, and verified staging deletion plus durable-object retention.
- The E2E uses disposable PGSTY Silo and ephemeral credentials; no production cloud account, live customer data or paid service is involved.

## Consequences and limits

- S3-backed staging removes the shared POSIX volume requirement for API/worker separation; it does not by itself prove production multi-node readiness.
- Production must separately configure least-privilege S3 and KMS permissions, lifecycle/retention, versioning, monitoring, backup/recovery and capacity limits.
- CI proves contract and adapter behavior against a disposable S3-compatible service, not production durability, RPO/RTO, multi-region behavior or customer traffic.
- The local backend remains available for single-node deployments and quickstart.
