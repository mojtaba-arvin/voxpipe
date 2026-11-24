# PostgreSQL Metadata Repository

## Deployment role model

`PostgresIngestionRepository` is the multi-node metadata adapter. It requires a PostgreSQL application role that:

- Is not a superuser.
- Does not have `BYPASSRLS`.
- Has only the required table privileges on ingestion tables and read access to `transcripts`, `transcript_speakers`, `transcript_segments`, and `transcript_words` for the `transcript:read` API capability.
- Does not own those tables.

The migration enables and forces row-level security. Every repository operation sets the transaction-local `voxpipe.tenant_id` setting, and every query also includes an explicit tenant predicate. Missing tenant context returns no rows under RLS. This protects against accidental query omissions; because the application credential can set the custom tenant context, it is not a defense against compromised application code. Strong isolation against a compromised service requires separate database roles/credentials or a trusted tenant-context boundary.

Recovery and reconciliation scans require an explicit `tenant_id`; the application role cannot perform cross-tenant scans. A scheduler should enumerate authorized tenant IDs through a separate tenant-control-plane boundary and invoke recovery per tenant.

## Migrations

Versioned SQL migrations live in `migrations/postgres/`:

1. `0001_create_ingestion_tables.sql` creates the session and asset tables with tenant-scoped uniqueness.
2. `0002_add_tenant_rls_and_updated_at.sql` adds update timestamps, recovery indexes, and forced RLS policies.
3. `0003_create_durable_ingestion_jobs.sql` adds the tenant-scoped durable job queue, composite tenant/session foreign key, lease invariants, claim/recovery indexes, and forced job-table RLS.

Apply them explicitly with:

```python
from voxpipe.ingestion import apply_migrations

applied = apply_migrations(database_dsn)
```

Migrations are recorded in `voxpipe_schema_migrations`, applied in version order, and each migration is transactional. The runner does not run automatically when the application starts.

## Application setup

After migrations, grant the non-owner application role:

- `USAGE` on the application schema.
- `SELECT, INSERT, UPDATE, DELETE` on the session, asset, and job tables.

Keep the DSN in a secret manager or environment variable. Never commit credentials. The CI workflow provisions a disposable PostgreSQL 16 service and uses a separate non-superuser role to test RLS.

## Repository behavior

- Idempotency is enforced by a unique `(tenant_id, idempotency_key)` constraint.
- Session transitions use state compare-and-swap.
- Lease claims use `SELECT ... FOR UPDATE` and an expiring owner token.
- Asset writes are idempotent for identical JSON payloads, including concurrent duplicate submissions.
- Durable jobs use atomic `SKIP LOCKED` claims, expiring leases, stale-owner fencing, bounded retries, and dead-letter terminalization.
- Go API and Python worker transitions use the same session-first row-lock order; queue-owned sessions are excluded from legacy recovery scans.
- Recovery and reconciliation list methods require a tenant ID and remain under RLS.
- Every connection is currently opened per operation. Add a bounded connection pool only after measuring real deployment load.

SQLite remains the zero-service local adapter. PostgreSQL is the intended multi-node metadata backend; the local filesystem object store remains single-node only.


## Upload leases and audit events (migration 0004)

Migration 0004 adds tenant-scoped upload leases and immutable audit events. Upload leases fence concurrent upload attempts and prevent finalization or cleanup while a transfer is active. The API reserves a lease before reading the request body and releases it after the transfer handler exits; crashed handlers leave an expiring lease that reconciliation respects.

Audit events record session creation/state transitions, upload start/finish, queueing, cancellation, cleanup, and retention deletion. Events are tenant-scoped with forced RLS and contain only lifecycle metadata, never media bytes, bearer tokens, or private storage references. The application role requires CRUD privileges on both new tables.
