"""Bootstrap a disposable, local-only VoxPipe PostgreSQL database."""
from __future__ import annotations
import os
import sys

def validate_database_target(database: str, is_superuser: bool) -> None:
    if database != "voxpipe_dev" or not is_superuser:
        raise SystemExit("refusing bootstrap: expected disposable voxpipe_dev and PostgreSQL superuser")


def main() -> int:
    admin_dsn = os.environ.get("VOXPIPE_ADMIN_DATABASE_DSN", "").strip()
    app_password = os.environ.get("VOXPIPE_LOCAL_APP_PASSWORD", "")
    if not admin_dsn or len(app_password) < 32:
        raise SystemExit("local admin DSN and a generated application password are required")
    import psycopg
    from psycopg import sql
    from voxpipe.ingestion import apply_migrations
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        database, is_superuser = connection.execute(
            "SELECT current_database(), (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"
        ).fetchone()
        validate_database_target(database, is_superuser)
        role_exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'voxpipe_app'").fetchone()
        role_sql = (
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS")
            if role_exists else
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS")
        )
        connection.execute(role_sql.format(sql.Identifier("voxpipe_app"), sql.Literal(app_password)))
    apply_migrations(admin_dsn)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute("GRANT USAGE ON SCHEMA public TO voxpipe_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_sessions, audio_assets, ingestion_jobs, ingestion_upload_leases, ingestion_audit_events TO voxpipe_app")
        connection.execute("GRANT SELECT, INSERT ON transcripts, transcript_speakers, transcript_segments, transcript_words, structured_extractions TO voxpipe_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE ON workflow_runs, workflow_steps TO voxpipe_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON transcript_segment_embeddings, provider_circuit_breakers TO voxpipe_app")
        attributes = connection.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'voxpipe_app'").fetchone()
        if attributes != (False, False):
            raise RuntimeError("voxpipe_app must remain NOSUPERUSER and NOBYPASSRLS")
    print("Local VoxPipe schema is migrated; voxpipe_app is NOSUPERUSER/NOBYPASSRLS.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
