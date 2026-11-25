# API health probes and local orchestration

## HTTP contract

The API exposes two unauthenticated, content-free endpoints:

- `GET /health/live` returns `200 {"status":"ok"}` when the HTTP process can serve requests. It does not depend on PostgreSQL.
- `GET /health/ready` returns `200 {"status":"ready"}` only when PostgreSQL is reachable and the core ingestion session/job tables are installed. It returns a generic `503 {"status":"not_ready"}` otherwise.

The readiness check has a two-second deadline. It does not return DSNs, database errors, schema names, tenant data, or credentials. Optional subsystems such as hybrid retrieval are not part of core API readiness: retrieval remains independently fail-closed and reports its own configuration/index errors through the retrieval contract.

These endpoints are intended for container/orchestrator probes and must not be used as customer-facing service guarantees. They do not assert worker progress, queue drain time, model availability, SLO compliance, or production capacity.

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