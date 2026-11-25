# S3-compatible storage deployment and recovery

## Scope and readiness boundary

S3-compatible storage is optional. Local filesystem storage remains the default for the guided quickstart and single-node development. The S3-backed topology allows the Go API and Python worker to run in separate containers without a shared staging filesystem.

The API exposes:
- `GET /health/live`: process liveness only;
- `GET /health/ready`: database/schema and configured staging backend readiness;
- `GET /health/dependencies`: content-free `database` and `object_storage` states.

A ready response proves the current API process can reach PostgreSQL and its configured staging backend. It does not prove object durability, backup completeness, queue drain SLOs, worker progress, RPO/RTO, multi-region replication or customer-traffic capacity.

## Runtime identities and permissions

Use separate runtime identities:

| Process | Credential variables | Allowed object scope |
|---|---|---|
| Go API | `VOXPIPE_S3_STAGING_ACCESS_KEY_ID`, `VOXPIPE_S3_STAGING_SECRET_ACCESS_KEY` | `staging/v1/*` only |
| Python worker | `VOXPIPE_S3_ACCESS_KEY_ID`, `VOXPIPE_S3_SECRET_ACCESS_KEY` | `staging/v1/*` and `tenant/*` |
| Provisioner | Provider/admin identity outside VoxPipe runtime | Bucket, lifecycle, versioning and identity administration |

The API rejects half-configured static credential pairs. If its staging pair is absent, the Go SDK uses the API process's own workload/default credential chain; it never falls back to the worker's VoxPipe credential variables. The worker similarly uses its own pair or workload identity.

The API policy needs object Put/Get/Delete only under `staging/v1/*). GetObject also authorizes HeadObject/checksum verification. It must not access `tenant/*`. The worker needs staging reads and cleanup plus durable tenant object Put/Get/Delete. The current runtime paths do not require bucket-wide object listing. Do not grant runtime identities bucket administration, lifecycle, versioning or policy-management permissions.

The disposable Silo CI provisions independent API and worker users and proves that the API identity is denied read, write and delete operations on a durable tenant object. Production policies must preserve the same prefix boundary.

## TLS, encryption and checksums

- Use HTTPS for every non-loopback endpoint. Plain HTTP is allowed only for loopback development/test endpoints.
- Explicitly configure server-side encryption (`AES256` or `aws:kms`) and a KMS key when using KMS.
- Ensure the worker identity and provisioner have the necessary KMS permissions for the selected key; do not grant key administration to the runtime identities.
- Preserve immutable conditional writes and SHA-256 verification. Never overwrite an existing staging object with different bytes.
- Do not log credentials, signed URLs, object keys, tenant identifiers, audio bytes or transcript content.

## Staging lifecycle policy

Application reconciliation is the primary cleanup mechanism. Bucket lifecycle expiry is only a safety net for abandoned staging objects; it must not decide whether a job is retryable.

Apply lifecycle expiry only to the `staging/v1/` prefix. Never attach a lifecycle rule to the whole bucket or `tenant/*` durable objects as a side effect of staging cleanup.

Calculate the minimum expiry as:

```text
minimum_days = ceil(
  (session_ttl_seconds
   + max_attempts * (job_lease_seconds + worker_max_backoff_seconds)
   + operator_recovery_window_seconds
   + safety_margin_days * 86400) / 86400
)
```

Use the actual deployment values. Increase the operator recovery window for slow incident response, long-running processing or a delayed reconciliation schedule. The 90-day expiry used by disposable CI is a test value, not a universal production recommendation. The CI validation uses a 30-day recovery window and a 7-day safety margin; its computed minimum is lower than 90 days.

A lifecycle policy that expires a live or retryable object is a deployment defect. Do not shorten the expiry to reduce storage costs without changing and revalidating the session, retry and recovery contract.

The API readiness probe uses a reserved, content-free object under `staging/v1/`. Lifecycle may eventually remove it; the next readiness probe recreates and verifies it. It is not associated with an ingestion session.

## Versioning and deletion semantics

The active VoxPipe object bucket is intentionally unversioned in the current deployment contract. With S3 versioning enabled, an ordinary `DeleteObject` creates a delete marker and leaves previous object bytes in noncurrent versions. VoxPipe's current durable-object deletion path does not yet purge every historical version.

Do not enable versioning on the active tenant-object bucket until hard-delete semantics and the privacy/retention policy for noncurrent versions are implemented and verified. If a provider requires versioning, define a separate privileged purge path for all versions and delete markers, with audit evidence and a documented retention policy.

The disposable CI uses a separate versioned recovery bucket to verify that an object version can be restored after a delete marker. This test bucket is not the application bucket and is not a production backup design.

## Recovery procedures

### Retryable ingestion or worker interruption

1. Inspect the tenant-scoped queue using `PYTHONPATH=src python scripts/inspect_ingestion_queue.py --tenant-id <UUID>`.
2. Do not manually delete a staging object while its session/job is pending, leased, retryable or otherwise nonterminal.
3. Allow lease fencing and queue retry to recover the job. The worker verifies the staged object's tenant, size and SHA-256 before durable commit.
4. After a session is terminal, run `PYTHONPATH=src python scripts/reconcile_ingestion.py --tenant-id <UUID>` from a trusted worker/operations environment.
5. If cleanup fails, preserve the object and rerun reconciliation after correcting the provider permission/outage. Reconciliation is idempotent.

### Ambiguous upload result

The Go API uses conditional object creation and checksum verification. If a client or API times out after the provider may have accepted a write, retry the same idempotent operation and let the API reconcile the existing object's size and SHA-256. Never overwrite the key manually.

### Object-store outage or permission denial

1. Check `/health/dependencies` for the content-free database/object-storage state.
2. Verify endpoint reachability, TLS, bucket existence, encryption configuration, KMS grants and the process-specific identity policy.
3. Do not copy credentials between API and worker to bypass a denied prefix.
4. Restore provider access, then allow queued jobs and reconciliation to retry. Preserve all nonterminal staging objects.

### Backup and restore

A production backup plan must coordinate PostgreSQL session/job/asset metadata with the durable object set. A database-only restore is incomplete if referenced object bytes are missing; an object-only restore is incomplete if database references are inconsistent. Define provider-specific backup, object version retention, restore validation, point-in-time consistency and operator ownership before production use.

Disposable Silo tests prove API compatibility and version-recovery mechanics only. They do not establish a production RPO/RTO, cross-region replication, provider durability or customer-traffic capacity.

## Migration and rollback

- Local storage remains the default and must continue to pass the guided quickstart.
- Switching an existing deployment to S3 requires a controlled copy of durable object bytes and verification of every database reference/checksum; changing the environment variable alone is not a migration.
- Keep the previous storage source available until object counts and checksums reconcile.
- Rollback must preserve the active database/object mapping. Do not point a database containing S3 object references at an empty local object directory.
- Do not remove the local adapter or shared-volume fallback until a deployment-specific migration and recovery rehearsal is complete.

## Disposable verification

The repository CI configures a staging-only lifecycle rule, checks its expiry against session/retry/recovery settings, verifies a separate versioned recovery bucket, provisions distinct API/worker identities, and runs the API/worker E2E without a shared staging volume. CI uses generated credentials and disposable Silo/PostgreSQL only; it does not select a production cloud account, bucket, region, KMS key or secret.
