# ADR-0046: Least-privilege S3 deployment and recovery

- **Status:** Accepted
- **Date:** 2026-11-25
- **Decision owners:** VoxPipe architecture
- **Tracking issue:** [VP-0046 / #157](https://github.com/mojtaba-arvin/voxpipe/issues/157)

## Context

VP-0044 introduced optional S3-compatible durable object storage. VP-0045 moved transient upload staging into the same object store and proved the Go API and Python queue worker can run in separate containers without a shared filesystem.

The first implementation deliberately uses one S3 credential pair for both API and worker. This is acceptable for the disposable integration topology but is not a least-privilege production deployment contract. The repository also relies on application reconciliation for cleanup without yet defining a safe bucket lifecycle backstop, versioning policy, or storage-specific readiness signal.

## Proposed decision

### 1. Separate runtime identities

- The Go API receives a staging-only identity. Its static credential pair, when used, is `VOXPIPE_S3_STAGING_ACCESS_KEY_ID` and `VOXPIPE_S3_STAGING_SECRET_ACCESS_KEY`. If these are absent, the API may use its own workload identity/default credential chain; it must never silently reuse the worker's `VOXPIPE_S3_ACCESS_KEY_ID` / `VOXPIPE_S3_SECRET_ACCESS_KEY` pair.
- The Python worker receives the durable-object identity through `VOXPIPE_S3_ACCESS_KEY_ID` and `VOXPIPE_S3_SECRET_ACCESS_KEY`, or its own workload identity/default credential chain.
- Reject half-configured static credential pairs. Do not log either identity, tokens, endpoint credentials, or provider error bodies.
- Keep endpoint, bucket, region, addressing, checksum and encryption configuration common, but keep credentials process-specific.
- The API identity is limited to `staging/v1/*`: conditional PutObject, GetObject/HeadObject for checksum verification, and DeleteObject for cancellation and cleanup of failed uploads. It must not access `tenant/*` durable assets.
- The worker identity can read/delete `staging/v1/*` and create/read/delete `tenant/*` durable objects. Reconciliation uses PostgreSQL session state and does not require bucket listing. Runtime identities should not receive bucket-administration, lifecycle, versioning, or policy-management permissions.
- Bucket administration belongs to a separate deployment/provisioning identity. Where supported, use workload roles rather than long-lived static keys.

### 2. Lifecycle and retention safety

- Application reconciliation remains the primary cleanup mechanism. S3 lifecycle expiration is only a safety net for abandoned objects and must never be the mechanism that decides whether a job is retryable.
- The `staging/v1/` lifecycle expiry must be longer than the maximum session lifetime, maximum retry/backoff window, and an explicit operator recovery window, plus a safety margin. Derive the minimum from configured limits; do not hard-code a short TTL.
- Never configure lifecycle rules that expire durable `tenant/*` objects as a side effect of staging cleanup.
- Lifecycle and versioning policy must be verified at deployment time and documented for the selected provider. If the provider cannot prove the required policy, report the storage deployment as not ready rather than guessing.
- Keep the active VoxPipe tenant-object bucket unversioned until the application has a verified hard-delete path for all object versions. Test version recovery in a separate versioned recovery bucket. If a production provider requires versioning, define a privileged all-version purge path and a privacy-compatible noncurrent-version retention policy before enabling it.

### 3. Content-free storage readiness

- Keep liveness independent from dependency readiness.
- Expose database and object-store readiness as separate content-free dependency states; do not expose bucket names, object keys, tenant IDs, credentials, or customer content.
- The readiness probe must use only permissions granted to the runtime identity. Avoid requiring broad bucket listing solely for health checks.
- Bound probe timeouts and avoid unbounded S3 requests on every health poll. Define cache/refresh behavior and stale-result semantics before implementation.

### 4. Recovery and operations

Document:
- runtime identity policies and provisioning-only permissions;
- TLS, encryption/KMS, bucket versioning and lifecycle requirements;
- safe staging retention calculation and reconciliation cadence;
- object-store outage, ambiguous upload, checksum mismatch, denied permission and cleanup-failure recovery;
- backup/restore and version-recovery procedures;
- migration and rollback from local storage or a shared-volume deployment;
- observable error/latency/cleanup aggregates without object keys or customer content.

## Verification

- Disposable Silo CI provisions distinct API and worker identities and proves the API cannot read or delete `tenant/*` durable objects.
- The worker can read staging, write durable objects and reconcile staging using its own identity.
- Tests prove missing/half-configured credentials and insecure non-loopback endpoints fail closed.
- Lifecycle tests verify the configured staging expiry exceeds session, retry and recovery windows; a live/retryable staging object is not removed by application reconciliation or the tested lifecycle policy.
- A separate versioned recovery bucket test proves an object can be restored after a delete marker; the active tenant-object bucket remains unversioned until hard-delete semantics are implemented.
- Fault-injection tests cover transient object-store outage, ambiguous write, denied permissions and cleanup retry.
- All CI uses disposable Silo and generated credentials. No production cloud account, live customer data, paid resource, RPO/RTO or multi-region durability claim is implied.

## Implementation progress

- PR #158 merged as `0ee0756fc67fea611eb59a62e43f2eca9381ff7c`; exact-head CI run `45629633269` passed all seven jobs. The Go API uses a staging-only credential pair or its own workload identity, the Python worker uses separate durable-object credentials, and disposable Silo policies prove the API identity cannot read, write or delete durable tenant objects.
- PR #159 merged as `a22c7e65acf64bb12d235db180b2d5d5c5da4ad6`; exact-head CI run `45633115543` passed all seven jobs. It adds separate database/object-storage readiness, lifecycle safety validation against session/retry/recovery windows, and a dedicated versioned recovery-bucket test. Post-merge main run `45633886907` passed all seven jobs.
- PR #160 merged as `0c61824b0c1bd6f2618c5d7e32f4310c10916df1`; exact-head CI run `45634043287` passed all seven jobs. Fault-injection coverage proves transient S3 reads are surfaced as sanitized storage errors and failed staging deletion remains retryable without losing source bytes or deleting the durable asset.
- Post-merge main validation run `45641155682` passed all seven jobs. VP-0046 is complete for repository scope; production deployment evidence remains target-specific.

The active tenant-object bucket remains unversioned until hard-delete semantics cover historical versions.

## Consequences and limits

- Separate credentials reduce the blast radius of a compromised API process and make the staging/durable boundary enforceable by the storage provider.
- Bucket lifecycle/versioning and storage readiness become explicit deployment contracts rather than undocumented operator assumptions.
- Disposable Silo can verify API compatibility and policy behavior, but it cannot establish real-account IAM correctness, production durability, backup RPO/RTO, multi-region replication or customer-traffic capacity.
- Local filesystem storage and the guided local quickstart remain the defaults.
