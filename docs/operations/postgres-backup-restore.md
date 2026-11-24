# PostgreSQL backup/restore rehearsal

## CI rehearsal

The repository validation workflow runs `scripts/verify_postgres_backup_restore.py` against a disposable PostgreSQL 16 database after applying all migrations. The script:

1. Inserts a reference-only workflow run/step and a provider circuit-breaker row into the disposable source database.
2. Creates a custom-format `pg_dump` backup using `--no-owner --no-acl`.
3. Restores into a fresh disposable database with `pg_restore --exit-on-error`.
4. Reapplies the explicit application-role grants and verifies the workflow journal, breaker state and forced-RLS metadata.
5. Connects as the NOBYPASSRLS `voxpipe_app` role and verifies that the restored workflow row is visible only with its tenant context.

The script refuses to run unless the source database is exactly `voxpipe_test`; the restore target is the hard-coded disposable `voxpipe_restore` database and is removed after the check. It executes the matching PostgreSQL 16 `pg_dump` and `pg_restore` binaries inside the service container, avoiding a client/server major-version mismatch. The only password supplied to Docker is the disposable CI service password defined in the workflow, never a production credential.

## Production requirements not covered by CI

This rehearsal proves a database-level logical dump/restore path on an ephemeral CI service. It does not configure or verify production backup scheduling, encryption at rest/in transit, off-host storage, immutable retention, key recovery, WAL archiving, point-in-time recovery, restore drills against production-sized data, RPO/RTO, or application/object-store consistency. Those require a deployment-specific backup destination and operational rehearsal before production acceptance.
