#!/usr/bin/env python3
"""Exercise a disposable PostgreSQL custom-format backup/restore round trip.

This is a CI rehearsal, not a production backup controller. It deliberately
refuses databases other than the disposable voxpipe_test source and the
hard-coded voxpipe_restore target.
"""
from __future__ import annotations

import json
import os
import subprocess
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg


SOURCE_DATABASE = "voxpipe_test"
RESTORE_DATABASE = "voxpipe_restore"


def _parts(dsn: str):
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("PostgreSQL DSN must be a postgres URI with a host")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError("PostgreSQL DSN must name a database")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


def _pg_env(parts: dict[str, str]) -> dict[str, str]:
    # Run matching PostgreSQL 16 client binaries inside the service container.
    return {
        "PGHOST": "localhost",
        "PGPORT": parts["port"],
        "PGUSER": parts["user"],
        "PGDATABASE": parts["database"],
    }


def _run_pg(
    command: list[str], env: dict[str, str], tool: str, *,
    container_id: str, input_data: bytes | None = None,
) -> bytes:
    docker_command = ["docker", "exec"]
    if input_data is not None:
        docker_command.append("-i")
    for key, value in env.items():
        docker_command.extend(["-e", f"{key}={value}"])
    # This credential is the disposable service password defined in the CI job.
    docker_command.extend(["-e", "PGPASSWORD=postgres", container_id, *command])
    try:
        completed = subprocess.run(
            docker_command, input=input_data, check=True, capture_output=True,
        )
        return completed.stdout
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"{tool} failed with exit code {exc.returncode}: {detail[-2000:]}") from None


def _grant_application_role(connection) -> None:
    connection.execute("GRANT USAGE ON SCHEMA public TO voxpipe_app")
    connection.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_sessions, audio_assets, "
        "ingestion_jobs, ingestion_upload_leases, ingestion_audit_events, ingestion_worker_heartbeats TO voxpipe_app"
    )
    connection.execute(
        "GRANT SELECT, INSERT ON transcripts, transcript_speakers, transcript_segments, "
        "transcript_words, structured_extractions TO voxpipe_app"
    )
    connection.execute("GRANT SELECT, INSERT, UPDATE ON workflow_runs, workflow_steps TO voxpipe_app")
    connection.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON transcript_segment_embeddings TO voxpipe_app"
    )
    connection.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON provider_circuit_breakers TO voxpipe_app"
    )


def main() -> None:
    admin_dsn = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN", "").strip()
    app_dsn = os.environ.get("VOXPIPE_TEST_POSTGRES_APP_DSN", "").strip()
    if not admin_dsn or not app_dsn:
        raise SystemExit("disposable admin and application PostgreSQL DSNs are required")
    admin = _parts(admin_dsn)
    app = _parts(app_dsn)
    if admin["database"] != SOURCE_DATABASE or app["database"] != SOURCE_DATABASE:
        raise SystemExit("refusing to run outside the disposable voxpipe_test database")
    if app["user"] != "voxpipe_app":
        raise SystemExit("application DSN must use the NOBYPASSRLS voxpipe_app role")
    container_id = os.environ.get("VOXPIPE_TEST_POSTGRES_CONTAINER_ID", "").strip()
    if not container_id:
        raise SystemExit("VOXPIPE_TEST_POSTGRES_CONTAINER_ID is required")

    tenant_id, other_tenant_id = str(uuid4()), str(uuid4())
    workflow_id, step_id = str(uuid4()), str(uuid4())
    worker_id = str(uuid4())
    provider = f"backup-restore-ci-{uuid4()}"

    with psycopg.connect(admin_dsn) as connection:
        connection.execute(
            "INSERT INTO workflow_runs "
            "(tenant_id, workflow_id, workflow_type, workflow_version, idempotency_key, state) "
            "VALUES (%s, %s, 'backup-restore-check', '1.0', %s, 'pending')",
            (tenant_id, workflow_id, f"backup-restore-{workflow_id}"),
        )
        connection.execute(
            "INSERT INTO workflow_steps "
            "(tenant_id, workflow_id, step_id, ordinal, step_name, state, input_refs, max_attempts) "
            "VALUES (%s, %s, %s, 0, 'backup.restore.probe', 'pending', %s::jsonb, 3)",
            (tenant_id, workflow_id, step_id, json.dumps({"probe": "reference-only"})),
        )
        connection.execute(
            "INSERT INTO provider_circuit_breakers (provider, consecutive_failures) VALUES (%s, 2)",
            (provider,),
        )
        connection.execute(
            "INSERT INTO ingestion_worker_heartbeats "
            "(tenant_id, worker_id, state, started_at, heartbeat_at, last_job_started_at) "
            "VALUES (%s, %s, 'busy', now() - interval '2 minutes', now() - interval '1 minute', now() - interval '1 minute')",
            (tenant_id, worker_id),
        )

    restore_dsn = urlunsplit((
        urlsplit(admin_dsn).scheme,
        urlsplit(admin_dsn).netloc,
        f"/{RESTORE_DATABASE}",
        urlsplit(admin_dsn).query,
        urlsplit(admin_dsn).fragment,
    ))
    restore_app_dsn = urlunsplit((
        urlsplit(app_dsn).scheme,
        urlsplit(app_dsn).netloc,
        f"/{RESTORE_DATABASE}",
        urlsplit(app_dsn).query,
        urlsplit(app_dsn).fragment,
    ))

    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(f"DROP DATABASE IF EXISTS {RESTORE_DATABASE} WITH (FORCE)")
        connection.execute(f"CREATE DATABASE {RESTORE_DATABASE}")

    try:
        dump_bytes = _run_pg([
            "pg_dump", "--no-password", "--format=custom", "--no-owner", "--no-acl",
        ], _pg_env(admin), "pg_dump", container_id=container_id)
        if not dump_bytes:
            raise RuntimeError("pg_dump produced an empty backup")

        restore_parts = {**admin, "database": RESTORE_DATABASE}
        _run_pg([
            "pg_restore", "--no-password", "--exit-on-error", "--no-owner", "--no-acl",
            "--dbname", RESTORE_DATABASE,
        ], _pg_env(restore_parts), "pg_restore", container_id=container_id, input_data=dump_bytes)

        with psycopg.connect(restore_dsn, autocommit=True) as connection:
            _grant_application_role(connection)
            run_state = connection.execute(
                "SELECT state FROM workflow_runs WHERE tenant_id = %s AND workflow_id = %s",
                (tenant_id, workflow_id),
            ).fetchone()
            step_state = connection.execute(
                "SELECT step_name, state FROM workflow_steps "
                "WHERE tenant_id = %s AND workflow_id = %s AND step_id = %s",
                (tenant_id, workflow_id, step_id),
            ).fetchone()
            breaker_state = connection.execute(
                "SELECT consecutive_failures FROM provider_circuit_breakers WHERE provider = %s",
                (provider,),
            ).fetchone()
            worker_state = connection.execute(
                "SELECT state, last_job_started_at FROM ingestion_worker_heartbeats "
                "WHERE tenant_id = %s AND worker_id = %s",
                (tenant_id, worker_id),
            ).fetchone()
            rls = connection.execute(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid = 'public.workflow_runs'::regclass"
            ).fetchone()
            worker_rls = connection.execute(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid = 'public.ingestion_worker_heartbeats'::regclass"
            ).fetchone()

        if run_state != ("pending",) or step_state != ("backup.restore.probe", "pending"):
            raise AssertionError("workflow journal rows did not survive restore")
        if breaker_state != (2,):
            raise AssertionError("provider breaker state did not survive restore")
        if worker_state is None or worker_state[0] != "busy" or worker_state[1] is None:
            raise AssertionError("worker heartbeat state did not survive restore")
        if rls != (True, True) or worker_rls != (True, True):
            raise AssertionError("forced row-level security did not survive restore")

        with psycopg.connect(restore_app_dsn) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_id,))
            visible = connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE workflow_id = %s", (workflow_id,)
            ).fetchone()[0]
            visible_worker = connection.execute(
                "SELECT count(*) FROM ingestion_worker_heartbeats WHERE worker_id = %s", (worker_id,)
            ).fetchone()[0]
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (other_tenant_id,))
            hidden = connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE workflow_id = %s", (workflow_id,)
            ).fetchone()[0]
            hidden_worker = connection.execute(
                "SELECT count(*) FROM ingestion_worker_heartbeats WHERE worker_id = %s", (worker_id,)
            ).fetchone()[0]
            if visible != 1 or hidden != 0 or visible_worker != 1 or hidden_worker != 0:
                raise AssertionError("restored workflow/worker rows violate tenant RLS isolation")

        print(
            "PostgreSQL backup/restore rehearsal passed: workflow run/step, provider breaker, "
            "worker heartbeat, forced RLS metadata and tenant-scoped visibility survived restore."
        )
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(f"DROP DATABASE IF EXISTS {RESTORE_DATABASE} WITH (FORCE)")


if __name__ == "__main__":
    main()
