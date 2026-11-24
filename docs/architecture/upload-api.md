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
- ingestion:read: read ingestion status and public asset metadata.
- transcript:read: search transcript segment text and provenance.

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

## MCP agent interface\n\nThe MCP stdio adapter is a thin client of the authenticated Go API. Configure `VOXPIPE_MCP_API_URL` and `VOXPIPE_MCP_API_TOKEN`; the token's tenant and scopes are authoritative. The adapter exposes session creation, status, asset metadata lookup, asset metadata search and transcript-segment lexical search. It does not transfer audio bytes or access PostgreSQL directly. Asset and transcript search responses omit tenant IDs and private storage references. Semantic/vector hybrid search is not yet exposed through this API. See [MCP capability contract](mcp-capabilities.md).\n\n## First deployment topology

The first staging adapter is a local filesystem. The Go API and Python worker must run on the same host and use the exact same VOXPIPE_STAGING_ROOT. This is a single-node topology; do not run API and workers on separate hosts or claim horizontal scalability until a shared object-storage staging adapter is implemented.

The Python worker uses VOXPIPE_OBJECT_ROOT for its local object store. This path is also single-node. PostgreSQL is the durable control plane, but local staging/object bytes are not multi-node storage.

## Run locally

Apply migrations using an administrative/schema-owner DSN, then configure a non-owner, non-superuser, NOBYPASSRLS application role with the required table privileges.

Go API:

    export VOXPIPE_DATABASE_DSN='postgresql://...'
    export VOXPIPE_STAGING_ROOT='./var/staging'
    export VOXPIPE_AUTH_MODE=oidc
    export VOXPIPE_OIDC_ISSUER='https://identity.example.com/'
    export VOXPIPE_OIDC_AUDIENCE='voxpipe-api'
    export VOXPIPE_OIDC_JWKS_URL='https://identity.example.com/.well-known/jwks.json'
    export VOXPIPE_OIDC_INTROSPECTION_URL='https://identity.example.com/oauth/introspect'
    export VOXPIPE_OIDC_CLIENT_ID='voxpipe-api'
    export VOXPIPE_OIDC_CLIENT_SECRET='...'
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
| VOXPIPE_AUTH_MODE | required | `oidc` in production; `static` only for bootstrap/development |
| VOXPIPE_OIDC_ISSUER | required in OIDC mode | Exact trusted token issuer |
| VOXPIPE_OIDC_AUDIENCE | required in OIDC mode | Expected API audience |
| VOXPIPE_OIDC_JWKS_URL | required in OIDC mode | HTTPS signing-key endpoint |
| VOXPIPE_OIDC_INTROSPECTION_URL | required in OIDC mode | HTTPS token active/revocation endpoint |
| VOXPIPE_OIDC_CLIENT_ID / CLIENT_SECRET | required in OIDC mode | Introspection client credentials |
| VOXPIPE_MAX_CONCURRENT_UPLOADS_PER_TENANT | 3 | Concurrent active upload leases per tenant |
| VOXPIPE_MAX_STAGED_BYTES_PER_TENANT | 2147483648 | Reserved staging bytes per tenant |
| VOXPIPE_MAX_INGESTIONS_PER_MINUTE_PER_TENANT | 60 | New ingestion sessions per rolling minute |
| VOXPIPE_UPLOAD_INACTIVITY_TIMEOUT_SECONDS | 60 | Maximum idle interval between body reads |
| VOXPIPE_UPLOAD_MAX_DURATION_SECONDS | 1800 | Absolute upload body-read duration |
| VOXPIPE_API_TOKEN_HASHES | required in static mode | SHA-256 digest to tenant/scope map; never plaintext tokens |
| VOXPIPE_STAGING_ROOT | ./var/staging | Private shared upload staging directory |
| VOXPIPE_MAX_UPLOAD_BYTES | 536870912 | Maximum upload size |
| VOXPIPE_SESSION_TTL_SECONDS | 86400 | Session lifetime |
| VOXPIPE_JOB_MAX_ATTEMPTS | 5 | Durable job attempt limit |
| VOXPIPE_OBJECT_ROOT | required by worker | Local object store root |
| VOXPIPE_TENANT_IDS | required by worker | Comma-separated trusted tenant allowlist |
| VOXPIPE_JOB_LEASE_SECONDS | 90 | Worker job lease duration |
| VOXPIPE_JOB_POLL_SECONDS | 1 | Idle worker polling interval |

## Known limitations before production

- Production identity uses OIDC RS256 JWT validation plus per-request token introspection. Static token mapping is an explicit bootstrap/development mode only.
- Local staging and object storage require API/worker co-location and a private service-owned filesystem.
- Pre-processing cancellation removes the staged source. Staged-source cleanup after expiry and after cancellation during finalizing/processing still needs a dedicated reconciler and explicit retention policy.
- Upload retry is whole-file and idempotent, not byte-range resumable.
- TLS termination remains the deployment ingress responsibility. Quotas are enforced by PostgreSQL advisory-lock reservations; tune defaults to deployment capacity. Local staging/object storage remain single-node.
- The API does not run migrations or create roles/grants automatically.


## Production authentication and upload controls

Production mode requires `VOXPIPE_AUTH_MODE=oidc`. The API validates RS256 JWT signatures against trusted JWKS, exact issuer and audience, expiry, not-before, UUID tenant mapping, and ingestion scopes. Unknown `kid` values trigger a JWKS refresh for key rotation. Every token is checked through OAuth 2.0 introspection, so revoked/inactive tokens and identity-provider failures fail closed. Provider endpoints must use HTTPS. `VOXPIPE_AUTH_MODE=static` is an explicit development/bootstrap mode only.

New ingestion sessions reserve their declared upload bytes before returning an upload URL. Reservation and rolling one-minute ingestion-rate checks are serialized per tenant in PostgreSQL. Reservations remain counted until reconciliation records `staging_cleanup_completed_at`, including terminal sessions whose staged files have not yet been cleaned. Active upload leases enforce the concurrent-upload ceiling. Default limits are 3 concurrent uploads, 2 GiB reserved staged bytes, and 60 new sessions per minute. Quota/rate failures return HTTP 429 with stable error codes; rate-limit responses include `Retry-After: 60`.

The API runner applies a resettable 60-second inactivity deadline and a 30-minute absolute body-read deadline by default. The inactivity deadline is reset on every read and capped by the absolute deadline, so a large upload can continue as long as progress is made. These limits are independently configurable. The API remains subject to the deployment ingress' own body-size and connection policies.
