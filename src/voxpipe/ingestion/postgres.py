"""PostgreSQL repository adapter and versioned migration runner.

psycopg is imported lazily so SQLite/local users do not need the PostgreSQL
driver installed merely to import the VoxPipe package.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from voxpipe.ingestion.repository import IngestionRepositoryConflict


def apply_migrations(
    dsn: str,
    migrations_dir: str | Path | None = None,
) -> tuple[int, ...]:
    """Apply ordered SQL migrations atomically and return newly applied versions."""
    import psycopg

    directory = Path(migrations_dir) if migrations_dir else (
        Path(__file__).resolve().parents[3] / "migrations" / "postgres"
    )
    if not directory.is_dir():
        raise ValueError("PostgreSQL migrations directory does not exist")

    applied_now: list[int] = []
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS voxpipe_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for migration in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            existing = connection.execute(
                "SELECT 1 FROM voxpipe_schema_migrations WHERE version = %s",
                (version,),
            ).fetchone()
            if existing:
                continue
            sql = migration.read_text(encoding="utf-8")
            with connection.transaction():
                for statement in sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        connection.execute(statement)
                connection.execute(
                    "INSERT INTO voxpipe_schema_migrations (version, name) VALUES (%s, %s)",
                    (version, migration.name),
                )
            applied_now.append(version)
    return tuple(applied_now)


class PostgresIngestionRepository:
    """Multi-node repository using PostgreSQL transactions and tenant RLS.

    The connecting role must not have BYPASSRLS. Every repository operation
    sets a transaction-local tenant context; recovery/reconciliation scans
    require an explicit tenant_id and never run cross-tenant under the app role.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def create_or_get_session(self, session: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant_id = session["tenant_id"]
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                INSERT INTO ingestion_sessions
                    (session_id, tenant_id, idempotency_key, state, payload, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::timestamptz)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    session["session_id"],
                    tenant_id,
                    session["idempotency_key"],
                    session["state"],
                    self._dump(session),
                    session["updated_at"],
                ),
            ).fetchone()
            if row is not None:
                return self._payload(row["payload"]), True

            existing = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (tenant_id, session["idempotency_key"]),
            ).fetchone()
            if existing is None:
                raise IngestionRepositoryConflict("session insert conflicted without a visible row")
            return self._payload(existing["payload"]), False

    def get_session_by_idempotency(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
        return self._payload(row["payload"]) if row else None

    def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND session_id = %s
                """,
                (tenant_id, session_id),
            ).fetchone()
        return self._payload(row["payload"]) if row else None

    def update_session(
        self,
        tenant_id: str,
        session_id: str,
        expected_state: str,
        updated_session: dict[str, Any],
    ) -> dict[str, Any]:
        if updated_session.get("tenant_id") != tenant_id:
            raise IngestionRepositoryConflict("updated session tenant does not match repository scope")
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE ingestion_sessions
                SET state = %s, payload = %s::jsonb, updated_at = %s::timestamptz
                WHERE tenant_id = %s AND session_id = %s AND state = %s
                RETURNING session_id
                """,
                (
                    updated_session["state"],
                    self._dump(updated_session),
                    updated_session["updated_at"],
                    tenant_id,
                    session_id,
                    expected_state,
                ),
            ).fetchone()
            if row is None:
                raise IngestionRepositoryConflict("session state changed concurrently")
        return updated_session

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        tenant_id = asset["tenant_id"]
        if not tenant_id:
            raise IngestionRepositoryConflict("asset tenant is required")
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                INSERT INTO audio_assets (asset_id, tenant_id, payload, updated_at)
                VALUES (%s, %s, %s::jsonb, %s::timestamptz)
                ON CONFLICT (asset_id) DO NOTHING
                RETURNING payload
                """,
                (
                    asset["asset_id"],
                    tenant_id,
                    self._dump(asset),
                    asset["updated_at"],
                ),
            ).fetchone()
            if row is not None:
                return self._payload(row["payload"])

            existing = connection.execute(
                """
                SELECT payload FROM audio_assets
                WHERE tenant_id = %s AND asset_id = %s
                """,
                (tenant_id, asset["asset_id"]),
            ).fetchone()
            if existing is None or self._payload(existing["payload"]) != asset:
                raise IngestionRepositoryConflict(
                    "asset identifier already exists with different content or tenant"
                )
            return self._payload(existing["payload"])

    def get_asset(self, tenant_id: str, asset_id: str) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload FROM audio_assets
                WHERE tenant_id = %s AND asset_id = %s
                """,
                (tenant_id, asset_id),
            ).fetchone()
        return self._payload(row["payload"]) if row else None

    def delete_asset(self, tenant_id: str, asset_id: str) -> bool:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                DELETE FROM audio_assets
                WHERE tenant_id = %s AND asset_id = %s
                RETURNING asset_id
                """,
                (tenant_id, asset_id),
            ).fetchone()
        return row is not None

    def claim_session(
        self,
        tenant_id: str,
        session_id: str,
        worker_id: str,
        now: str,
        lease_expires_at: str,
    ) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT state, payload FROM ingestion_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (tenant_id, session_id),
            ).fetchone()
            if row is None or row["state"] in {"completed", "failed", "cancelled", "expired"}:
                return None

            session = self._payload(row["payload"])
            if session.get("tenant_id") != tenant_id:
                raise IngestionRepositoryConflict("session payload tenant does not match repository scope")
            prior_lease = session.get("lease") or {}
            prior_expiry = prior_lease.get("expires_at")
            if prior_expiry and prior_expiry > now and prior_lease.get("owner_id") != worker_id:
                return None

            session["lease"] = {
                "owner_id": worker_id,
                "expires_at": lease_expires_at,
                "heartbeat_at": now,
                "attempt": int(prior_lease.get("attempt", 0)) + 1,
            }
            session["updated_at"] = now
            connection.execute(
                """
                UPDATE ingestion_sessions
                SET payload = %s::jsonb, updated_at = %s::timestamptz
                WHERE tenant_id = %s AND session_id = %s
                """,
                (self._dump(session), now, tenant_id, session_id),
            )
            return session

    def list_recoverable_sessions(
        self,
        now: str,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant = self._required_tenant(tenant_id)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s
                  AND state = ANY(%s)
                  AND NOT EXISTS (
                    SELECT 1 FROM ingestion_jobs AS job
                    WHERE job.tenant_id = ingestion_sessions.tenant_id
                      AND job.session_id = ingestion_sessions.session_id
                  )
                  AND (
                    state <> 'created'
                    OR (
                        COALESCE((payload #>> '{upload,received_size_bytes}')::bigint, 0) > 0
                        AND (payload #>> '{upload,received_size_bytes}')::bigint =
                            (payload #>> '{upload,expected_size_bytes}')::bigint
                    )
                  )
                  AND (
                    payload #>> '{lease,expires_at}' IS NULL
                    OR (payload #>> '{lease,expires_at}')::timestamptz <= %s::timestamptz
                  )
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (tenant, ["created", "receiving", "finalizing", "queued", "processing"], now, limit),
            ).fetchall()
        return [self._payload(row["payload"]) for row in rows]

    def list_sessions_by_state(
        self,
        state: str,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant = self._required_tenant(tenant_id)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND state = %s
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (tenant, state, limit),
            ).fetchall()
        return [self._payload(row["payload"]) for row in rows]

    @contextmanager
    def _tenant_connection(self, tenant_id: str) -> Iterator[Any]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('voxpipe.tenant_id', %s, true)",
                (tenant_id,),
            )
            yield connection

    @staticmethod
    def _required_tenant(tenant_id: str | None) -> str:
        if tenant_id is None:
            raise ValueError("tenant_id is required for PostgreSQL RLS-scoped scans")
        return tenant_id

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        raise TypeError("PostgreSQL JSONB payload has an unsupported representation")
