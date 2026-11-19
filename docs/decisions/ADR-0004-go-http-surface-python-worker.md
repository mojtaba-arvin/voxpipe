# ADR-0004: Go HTTP Surface and Python Ingestion Worker

- **Status:** Accepted for the first authenticated batch-ingestion API
- **Date:** 2026-11-19
- **Decision owners:** VoxPipe architecture

## Context

The initial architecture assigns the public API, gateway, and concurrent I/O surface to Go, while Python owns media/AI application logic and evaluation. The repository currently has a Python ingestion service and PostgreSQL adapters, but no HTTP server. VP-0015 introduces a public authenticated upload surface and asynchronous processing.

Using Python for the API would shorten the first implementation but silently reverse the intended language boundary. Implementing a second Python API/worker stack would also make the queue/session contracts harder to enforce across runtimes.

## Decision

- Implement the public HTTP API in Go using the standard `net/http` server and a PostgreSQL driver.
- Keep media inspection, decode validation, object-store integration, and ingestion state transitions in the existing Python application service.
- Go and Python share only versioned JSON/SQL contracts; neither runtime imports the other's implementation.
- PostgreSQL remains the durable control plane for sessions and jobs. Queue claims use `SKIP LOCKED`, expiring leases, and tenant RLS.
- The first upload staging adapter is a private, service-owned filesystem directory shared by the API and Python worker on one host. It is explicitly single-node and must not be represented as horizontally scalable.
- The HTTP API derives tenant identity from an authenticated principal. Client payloads never contain authoritative tenant IDs, local paths, or object keys.
- The API streams request bytes to a generated staging path, enforces configured and declared byte limits, computes SHA-256, and atomically publishes the staged file before allowing finalization.
- Finalization updates the session and inserts the durable job in one PostgreSQL transaction.

## Why not a distributed staging abstraction yet?

The existing object-storage port has a local adapter only. Introducing S3/MinIO now would add credentials, deployment configuration, lifecycle policy, and another integration surface before a working upload lifecycle exists. The private local staging adapter is a bounded first slice; a shared/object-store staging port becomes a separate adapter task before multi-node API/worker deployment.

## Consequences

Positive:
- Preserves the intended Go shell / Python brain split.
- Avoids a Python HTTP server as a permanent public boundary.
- Gives the API a small, static runtime and explicit concurrency controls.
- Allows worker and API to scale independently after staging moves to shared object storage.

Costs and constraints:
- The Go service duplicates only the narrow SQL contract needed for session creation, finalization, status, and cancellation; SQL schema and JSON payload shape must be contract-tested.
- First deployment requires API and workers to share a private staging volume on one host.
- Tenant authentication requires an explicit identity-provider adapter; development token maps must never be treated as production identity infrastructure.
- No production deployment is authorized by this ADR.

## Revisit when

- A real multi-node deployment requires shared staging/object storage.
- Measured API workload requires a different transport/runtime.
- The Python worker is replaced by a Go speech pipeline behind stable contracts.
