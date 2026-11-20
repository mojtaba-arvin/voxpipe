#!/usr/bin/env python3
"""Prepare an isolated PostgreSQL database for Go integration tests.

This script is destructive only to schema state when the caller explicitly
points it at a disposable test database. It never reads production settings.
"""
from __future__ import annotations

import os


def main() -> None:
    admin_dsn = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN", "").strip()
    if not admin_dsn:
        raise SystemExit("VOXPIPE_TEST_POSTGRES_ADMIN_DSN is required")

    import psycopg

    from voxpipe.ingestion import apply_migrations

    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        if database_name != "voxpipe_test":
            raise SystemExit("refusing to prepare a database other than the disposable voxpipe_test database")
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'voxpipe_app'"
        ).fetchone()
        if role_exists is None:
            connection.execute(
                "CREATE ROLE voxpipe_app LOGIN PASSWORD 'voxpipe_app' NOSUPERUSER NOBYPASSRLS"
            )
        else:
            connection.execute(
                "ALTER ROLE voxpipe_app WITH LOGIN PASSWORD 'voxpipe_app' NOSUPERUSER NOBYPASSRLS"
            )

    apply_migrations(admin_dsn)

    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute("GRANT USAGE ON SCHEMA public TO voxpipe_app")
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_sessions, audio_assets, ingestion_jobs, ingestion_upload_leases, ingestion_audit_events TO voxpipe_app"
        )

    print("PostgreSQL integration database prepared with forced-RLS application role.")


if __name__ == "__main__":
    main()
