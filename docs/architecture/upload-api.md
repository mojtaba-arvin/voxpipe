# Authenticated Batch Upload API

## Runtime split

- The public HTTP surface is Go (cmd/voxpipe-api) using net/http.
- Session/job metadata is stored in PostgreSQL through the Go store adapter.
- Media inspection, full decode validation, immutable object storage, and ingestion lifecycle remain in the Python worker.
- The Go and Python processes share only PostgreSQL rows, versioned JSON payloads, and the private staging filesystem. They do not import each other's code.

The API does not apply database migrations at startup. Apply migrations with the Python migration runner before starting either process.

## Authentication

Every route requires a bearer token and an explicit capability:
- ingestion:write: create, upload, finalize, cancel.
- ingestion:read: read status.

The initial authenticator accepts SHA-256 token digests mapped to tenant UUIDs and scopes. Configure VOXPIPE_API_TOKEN_HASHES as semicolon-separated entries:

    <sha256-token-digest>:<tenant-uuid>:ingestion:read,ingestion:write

Generate the digest without writing the raw token to a file:

    printf %s "$VOXPIPE_API_TOKEN" | sha256sum

Only the digest belongs in this mapping. The raw bearer token is supplied by the caller. Do not commit tokens or digests to the repository. The static digest map is a bootstrap adapter, not a full identity platform; production deployments should replace it with an OIDC/JWT authenticator that implements the same Authenticator interface and derives tenant/scopes from verified claims. Retention policy is deliberately not client-selectable until a policy registry and enforcement path are implemented.

## Upload lifecycle

1. POST /v1/ingestions creates an idempotent session. The caller supplies expected byte size, SHA-256, and allowlisted source metadata. Tenant ID, asset ID, staging path, object key, and retention policy are server-controlled.
2. PUT /v1/ingestions/{session_id}/content streams raw bytes to a private temporary file. The stager enforces configured size limits, verifies declared size and SHA-256, fsyncs, and publishes with atomic create-if-absent semantics.
3. POST /v1/ingestions/{session_id}/finalize re-verifies staged bytes and changes the session to finalizing while inserting one pending job in the same PostgreSQL transaction.
4. GET /v1/ingestions/{session_id} returns tenant-scoped session/job status.
5. DELETE /v1/ingestions/{session_id} atomically cancels the session and any pending/leased job.

The API returns after durable job submission. It never holds the upload HTTP request open while FFmpeg or model processing runs. Interrupted transfers remove their temporary file; the client retries the complete upload from byte zero. Byte-range/resumable chunk uploads are deliberately deferred.

## MCP agent interface\n\nThe MCP stdio adapter is a thin client of the authenticated Go API. Configure `VOXPIPE_MCP_API_URL` and `VOXPIPE_MCP_API_TOKEN`; the token's tenant and scopes are authoritative. The adapter exposes session creation, status, asset metadata lookup, and metadata search. It does not transfer audio bytes or access PostgreSQL directly. Asset responses omit tenant IDs and private storage references. Search currently covers asset ID, source kind, MIME type, container format, and codec only; transcript and semantic search are not implemented. See [MCP capability contract](mcp-capabilities.md).\n\n## First deployment topology

The first staging adapter is a local filesystem. The Go API and Python worker must run on the same host and use the exact same VOXPIPE_STAGING_ROOT. This is a single-node topology; do not run API and workers on separate hosts or claim horizontal scalability until a shared object-storage staging adapter is implemented.

The Python worker uses VOXPIPE_OBJECT_ROOT for its local object store. This path is also single-node. PostgreSQL is the durable control plane, but local staging/object bytes are not multi-node storage.

## Run locally

Apply migrations using an administrative/schema-owner DSN, then configure a non-owner, non-superuser, NOBYPASSRLS application role with the required table privileges.

Go API:

    export VOXPIPE_DATABASE_DSN='postgresql://...'
    export VOXPIPE_STAGING_ROOT='./var/staging'
    export VOXPIPE_API_TOKEN_HASHES='<digest>:<tenant-uuid>:ingestion:read,ingestion:write'
    go run ./cmd/voxpipe-api

Python worker (same database, staging, and object paths):

    export VOXPIPE_DATABASE_DSN='postgresql://...'
    export VOXPIPE_STAGING_ROOT='./var/staging'
    export VOXPIPE_OBJECT_ROOT='./var/objects'
    export VOXPIPE_TENANT_IDS='<authorized-tenant-uuid>'
    PYTHONPATH=src python scripts/run_ingestion_worker.py

The worker tenant list is an explicit allowlist supplied by a trusted scheduler/control plane. Neither the API nor worker uses a cross-tenant database scan under the application role.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| VOXPIPE_LISTEN_ADDR | :8080 | Go HTTP listen address |
| VOXPIPE_DATABASE_DSN | required | PostgreSQL application DSN |
| VOXPIPE_API_TOKEN_HASHES | required | SHA-256 digest to tenant/scope map |
| VOXPIPE_STAGING_ROOT | ./var/staging | Private shared upload staging directory |
| VOXPIPE_MAX_UPLOAD_BYTES | 536870912 | Maximum upload size |
| VOXPIPE_SESSION_TTL_SECONDS | 86400 | Session lifetime |
| VOXPIPE_JOB_MAX_ATTEMPTS | 5 | Durable job attempt limit |
| VOXPIPE_OBJECT_ROOT | required by worker | Local object store root |
| VOXPIPE_TENANT_IDS | required by worker | Comma-separated trusted tenant allowlist |
| VOXPIPE_JOB_LEASE_SECONDS | 90 | Worker job lease duration |
| VOXPIPE_JOB_POLL_SECONDS | 1 | Idle worker polling interval |

## Known limitations before production

- Static token mapping must be replaced or integrated with the deployment identity provider.
- Local staging and object storage require API/worker co-location and a private service-owned filesystem.
- Pre-processing cancellation removes the staged source. Staged-source cleanup after expiry and after cancellation during finalizing/processing still needs a dedicated reconciler and explicit retention policy.
- Upload retry is whole-file and idempotent, not byte-range resumable.
- No TLS termination, rate limiter, per-tenant quota service, or distributed upload storage is included in this first API slice; deploy behind a trusted TLS ingress and add explicit quota policy before internet exposure.
- The API does not run migrations or create roles/grants automatically.
