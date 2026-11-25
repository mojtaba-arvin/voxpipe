# ADR-0044: Shared S3-Compatible Object Storage

- **Status:** Accepted
- **Date:** 2026-11-25
- **Decision owners:** VoxPipe architecture

## Context

The existing `ObjectStoragePort` has an atomic local filesystem adapter. It is appropriate for development and a single API/worker host, but separate hosts cannot safely share its object namespace. Multi-node API and worker deployment therefore requires a shared object store before the topology can be called horizontally deployable.

The current upload limit is 512 MiB. S3-compatible single-request `PutObject` supports this size without multipart upload, which avoids multipart lifecycle state and ambiguous completion semantics in the first adapter.

## Proposed decision

- Implement an optional `S3CompatibleObjectStore` behind the existing `ObjectStoragePort`; retain the local adapter as the default.
- Use the S3 API through an optional boto3/botocore dependency constrained to the supported major version and covered by the dependency audit. SDK objects, bucket names, endpoint URLs, credentials and signed URLs must not escape the adapter.
- Support standard AWS S3 and S3-compatible endpoints through explicit endpoint, region, bucket and addressing configuration. Do not select a provider, account, region, bucket or endpoint by default.
- Keep object keys opaque and tenant-scoped by trusted application code. The adapter validates keys but does not accept tenant identity as an authorization decision.
- Spool each incoming stream to a bounded, private temporary file while computing byte count and SHA-256. This keeps memory bounded and supplies a known Content-Length for the single-request upload.
- Keep the current maximum object size at or below the S3 single-PUT limit. Do not add multipart upload until a measured requirement justifies the added state machine and abort/reconciliation policy.
- Use conditional create (`If-None-Match: *`) for immutable writes. If a key already exists, compare verified size and SHA-256: identical content is idempotent; different content is a conflict. Explicit overwrite remains an explicit caller choice.
- Send an S3 SHA-256 checksum and persist the digest as object metadata. Verify returned size and checksum metadata before reporting success; fail closed when the configured endpoint cannot provide the required integrity contract.
- Require explicit server-side encryption configuration when the S3 backend is selected. Support AES256 and aws:kms; require a KMS key identifier for aws:kms. Never log secret values.
- Bound connection/read timeouts and SDK retry attempts. Retry only operations whose idempotency is established; conditional immutable writes reconcile ambiguous outcomes by reading the existing object and comparing its checksum.
- Map provider errors to the existing storage exceptions without leaking provider response bodies or credentials.

## Runtime composition

A single storage factory selects `local` or `s3_compatible` from explicit configuration. API, ingestion worker, workflow worker and reconciliation tooling must use the same factory and storage configuration. The local quickstart remains local and requires no cloud credentials.

The S3 backend is opt-in and must fail during startup if its optional SDK, bucket, endpoint, credentials, checksum support or encryption requirements are invalid. It must never silently fall back to local storage.

## Verification

- Deterministic fake-client tests cover streaming, size bounds, checksum mismatch, conditional-create races, idempotent retry, conflict, explicit overwrite, delete and error redaction.
- CI runs a disposable S3-compatible service with no external egress or cloud credentials.
- Two independently constructed adapter instances must observe the same object and tenant key namespace.
- CI verifies that encryption headers and conditional-write semantics are sent and that unsupported required capabilities fail closed.
- Existing local storage, guided quickstart, ingestion, retention and backup/restore tests remain green.

## Consequences and limits

- Shared object bytes remove the local-volume requirement for API/worker object access, but staging files and other node-local runtime concerns must still be evaluated separately.
- CI proves adapter and protocol behavior against the disposable implementation; it does not prove production cloud durability, IAM policy correctness, lifecycle configuration, backup RPO/RTO, multi-region behavior or customer traffic capacity.
- Production deployment must separately configure least-privilege bucket policy, encryption/KMS, versioning/retention, lifecycle cleanup, monitoring and recovery procedures.
