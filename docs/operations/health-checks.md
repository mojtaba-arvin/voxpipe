# API health probes and local orchestration

## HTTP contract

The API exposes three unauthenticated, content-free endpoints:

- `GET /health/live` returns `200 {"status":"ok"}` when the HTTP process can serve requests. It does not depend on PostgreSQL or object storage.
- `GET /health/ready` returns `200 {"status":"ready"}` only when PostgreSQL/schema and the configured staging backend are reachable. It returns a generic `503 {"status":"not_ready"}` otherwise. The Docker health check uses this endpoint.
- `GET /health/dependencies` reports separate `database` and `object_storage` states, each either `ready` or `not_ready`, with an overall status and HTTP 200/503. It never includes provider errors or configuration values.

The readiness checks share a two-second deadline and run independently. They do not return DSNs, database errors, schema names, bucket names, object keys, tenant data, or credentials. Optional subsystems such as hybrid retrieval are not part of core API readiness: retrieval remains independently fail-closed and reports its own configuration/index errors through the retrieval contract.

For local staging, the API creates, syncs, reads and removes a small private probe file under the configured staging root; a read-only or unavailable staging volume therefore fails readiness.

For S3-compatible staging, the API verifies a reserved, content-free probe object under the staging namespace. It creates the probe if it is missing (for example, after lifecycle expiry) and verifies its checksum on subsequent checks. The API identity needs only its staging-prefix permissions; no bucket-wide listing permission is required. The probe is not associated with an ingestion session and is never returned by the API.

These endpoints are intended for container/orchestrator probes and must not be used as customer-facing service guarantees. They do not assert worker progress, queue drain time, model availability, SLO compliance, or production capacity.

## Worker liveness and progress view

The worker supervisor registers one tenant-scoped heartbeat row per explicitly authorized tenant and refreshes it every 15 seconds from a thread independent of job execution and job-lease renewal. Graceful shutdown marks the row stopped. A crash leaves the last state and timestamp in place; after 45 seconds without a heartbeat it is counted as stale.

The tenant-authorized `GET /v1/operations/ingestion/summary` response includes a `workers` aggregate with active/stale, idle/busy and stale-busy counts plus the oldest busy duration. It never returns worker IDs or tenant identifiers. The states distinguish an idle live worker from a busy live worker and from a process that stopped refreshing its heartbeat.

This is liveness and job-state visibility, not proof that audio processing is making semantic progress. A live busy worker may be processing a legitimately long job. Correlate busy age with queue age and lease state; do not terminate or replay work solely because a job is old. Heartbeat aggregates are available to operator tooling, but automated alert delivery remains unconfigured until an exporter and alert manager are deployed.

## Container and Compose behavior

The API image includes a Docker `HEALTHCHECK` that invokes the statically linked API binary in `healthcheck` mode. The probe calls the loopback readiness endpoint on the port selected by `VOXPIPE_LISTEN_ADDR`; it requires no shell, curl, wget, credentials, or external network access.

The local Compose worker waits for the API service to become healthy. The dependency chain is therefore:

1. PostgreSQL accepts connections.
2. The bootstrap service applies migrations and provisions the least-privilege application role.
3. Private storage volumes are initialized.
4. The API starts and becomes ready only after PostgreSQL and the core schema are available.
5. The worker starts after API readiness.

A running worker container only proves that its process has not exited; it does not prove that it is claiming jobs or making progress. Worker heartbeats, queue-age alert delivery, and staging SLO measurement remain separate operational work. Do not interpret API readiness as worker health.

## Security

Health endpoints are intentionally unauthenticated because orchestrators need to probe them before application credentials are available. They expose only a bounded status value. Do not add dependency error strings, build environment, tenant identifiers, queue contents, model names, or storage paths to these responses.