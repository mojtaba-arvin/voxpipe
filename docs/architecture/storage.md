# Object Storage Boundary

## Port

`ObjectStoragePort` is a provider-neutral interface:

- `put(key, stream, expected_sha256, overwrite)` stores an immutable object and returns its key, size, and SHA-256 digest.
- `open(key)` returns a caller-owned binary stream.
- `head(key)` returns current size and digest.
- `delete(key)` removes an object and reports whether it existed.

Provider SDK classes, bucket clients, signed URLs, and vendor-specific metadata must not cross this boundary.

## Object key rules

- Keys are relative POSIX-style paths scoped by tenant and asset.
- Keys are opaque references, not URLs and never contain credentials.
- Absolute paths, drive prefixes, backslashes, empty segments, dot segments, and control characters are rejected.
- Tenant identity and key prefix must be derived by trusted application code, not accepted from a client as an authorization decision.

## Local filesystem adapter

`LocalFilesystemObjectStore` is for development and single-node deployments:

- Writes stream into a temporary file in the target directory.
- The file is flushed and fsynced before an atomic create-if-absent hard link or explicit atomic replace.
- Repeated writes of identical content to the same key are idempotent.
- Different content at an existing key conflicts unless overwrite is explicitly enabled.
- Optional expected SHA-256 is verified before publication.
- Reads and `head` calculate the current digest; callers can compare it with the asset integrity field.
- Temporary files are removed on failure.
- Symlinked parents/targets and path traversal are rejected.

The root directory must be owned by the service account and not writable by untrusted users. The adapter checks symlinks and resolved paths but is not a defense against a hostile process racing filesystem changes under a shared writable root. Production deployments should isolate the worker and use a dedicated object-storage adapter.

## Integrity and deletion

The storage adapter returns a content digest; the asset metadata stores that digest as the canonical integrity reference. Retrieval can compare the current object digest with the asset record before processing.

Deletion is one operation in a larger asset lifecycle. The application layer must coordinate deletion of object bytes, database metadata, transcripts, embeddings, caches, and provider copies. A local object delete does not imply that derived data has been deleted.


## Storage readiness probe

The API's `/health/ready` includes the configured staging backend. In S3-compatible mode it verifies a reserved content-free probe object at the system-reserved staging reference derived from fixed non-tenant UUIDs. The API creates the small probe object if it is absent (for example, after staging lifecycle expiry), then verifies its size and SHA-256. The object is not linked to an ingestion session and is never exposed by public API responses.

The probe uses only the API staging identity's existing `PutObject`, `GetObject`/HeadObject and checksum permissions under `staging/v1/*`; it does not require bucket-wide listing. `GET /health/dependencies` reports separate database and object-storage readiness states without exposing provider errors, bucket names, keys or tenant identifiers. The probe is a reachability/configuration signal, not a durability or capacity guarantee.

## Runtime credential separation

The Go API and Python worker must not share one long-lived S3 credential pair:

- Go API staging credentials: `VOXPIPE_S3_STAGING_ACCESS_KEY_ID` and `VOXPIPE_S3_STAGING_SECRET_ACCESS_KEY`. Configure this pair only in the API process. If omitted, the Go SDK uses that process's workload/default credential chain; it never falls back to the worker pair.
- Python worker durable-object credentials: `VOXPIPE_S3_ACCESS_KEY_ID` and `VOXPIPE_S3_SECRET_ACCESS_KEY`. Configure this pair only in the worker process, or use the worker's own workload/default credential chain.

Reject half-configured static pairs. The API identity should be restricted to `staging/v1/*`; the worker identity needs staging read/cleanup and durable `tenant/*` object operations. Bucket lifecycle, versioning and policy administration belong to a separate provisioning identity, not either runtime process. See [ADR-0046](../decisions/ADR-0046-s3-deployment-recovery.md).

## S3-compatible adapter

`S3CompatibleObjectStore` implements the same opaque-key port for shared object bytes. It is optional and never selected implicitly:

- `VOXPIPE_STORAGE_BACKEND=local` is the default and preserves the guided quickstart.
- `VOXPIPE_STORAGE_BACKEND=s3_compatible` selects the S3 API adapter and fails closed if configuration or the optional SDK is missing.
- Install the optional SDK with `pip install -r requirements-s3.txt`.
- Configure `VOXPIPE_S3_BUCKET`, `VOXPIPE_S3_REGION`, and `VOXPIPE_S3_SERVER_SIDE_ENCRYPTION` (`AES256` or `aws:kms`). KMS mode also requires `VOXPIPE_S3_KMS_KEY_ID`; configure the canonical key ID/ARN returned by the provider (not an alias), which the adapter verifies on reads.
- `VOXPIPE_S3_ENDPOINT_URL` is optional for standard AWS S3 and required for most self-hosted S3-compatible services. Non-loopback endpoints must use HTTPS. Path-style addressing is the default; `VOXPIPE_S3_ADDRESSING_STYLE` may be `path` or `virtual`.
- Credentials are supplied as a pair through `VOXPIPE_S3_ACCESS_KEY_ID` and `VOXPIPE_S3_SECRET_ACCESS_KEY`, or obtained from the SDK's default credential chain (for example, an AWS workload role). Never embed credentials in the endpoint URL.
- Connection/read timeouts and SDK retry attempts are bounded by `VOXPIPE_S3_CONNECT_TIMEOUT_SECONDS`, `VOXPIPE_S3_READ_TIMEOUT_SECONDS`, and `VOXPIPE_S3_MAX_ATTEMPTS`.

The adapter spools each incoming stream to a private temporary file to calculate length and SHA-256 with bounded memory. It uses one conditional `PutObject` request, provider SHA-256 checksums and required server-side encryption. Same-content retries are idempotent; different content at an immutable key conflicts. It verifies provider checksum data (or streams a GET when the endpoint omits checksum headers) before returning object metadata. The current 512 MiB limit is below the S3 single-PUT maximum of 5 GiB; multipart upload is deliberately not implemented.

For the ingestion worker container, build the optional SDK into the image only when S3 is selected: `docker build --build-arg VOXPIPE_INSTALL_S3=1 -f Dockerfile.worker -t voxpipe-worker-s3 .`. The default image does not install boto3, and the local Compose quickstart remains unchanged. Host-based workflow workers must install `requirements-s3.txt` in their runtime environment when configured for S3.

The local root setting remains entrypoint-specific for backward compatibility: `VOXPIPE_OBJECT_ROOT` for the ingestion worker and `VOXPIPE_OBJECT_STORE_ROOT` for the speech workflow worker and reconciliation command. S3 mode ignores these local object-root settings. VP-0045 adds S3-backed upload staging so `VOXPIPE_STAGING_ROOT` is not required for new S3-backed sessions; keep it mounted during migration while legacy local sessions remain.

The Python ingestion worker, speech workflow worker and reconciliation command use the S3 adapter through one storage factory. VP-0045 (issue #153; merged in PRs #155 and #156) adds the matching Go API stager using the same `VOXPIPE_STORAGE_BACKEND` and shared endpoint/bucket/region/encryption configuration while keeping credentials process-specific. It persists `staging_backend` and `staging_reference` in the tenant-scoped ingestion session; S3 keys use `staging/v1/{tenant_uuid}/{session_uuid}/source`. The Python worker verifies and materializes the source to private worker-local temporary disk for media inspection, then writes the durable audio object. Terminal-session reconciliation deletes the staging object only after durable processing has completed; retryable sessions retain it. Local staging remains the default. In S3 mode, the API and worker operate in separate containers without a shared POSIX staging or object volume.

CI exercises a disposable S3-compatible endpoint, separate least-privilege identities, lifecycle and version-recovery behavior, separate-container upload/processing, and injected transient read/cleanup failures without live cloud credentials, external egress or paid resources. This does not prove production bucket policy/IAM correctness in a real account, KMS configuration, provider durability, backup RPO/RTO, multi-region behavior or customer-traffic capacity. Configure and rehearse those controls in the target deployment; keep local storage as the default for the guided quickstart.
