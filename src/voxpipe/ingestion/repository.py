"""Tenant-scoped SQLite repository for ingestion sessions and audio assets."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol


class IngestionRepositoryError(Exception):
    code = "INGESTION_REPOSITORY_ERROR"


class IngestionRepositoryConflict(IngestionRepositoryError):
    code = "INGESTION_REPOSITORY_CONFLICT"


class IngestionRepositoryPort(Protocol):
    def create_or_get_session(self, session: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...
    def get_session_by_idempotency(self, tenant_id: str, idempotency_key: str) -> dict[str, Any] | None: ...
    def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None: ...
    def update_session(self, tenant_id: str, session_id: str, expected_state: str, updated_session: dict[str, Any]) -> dict[str, Any]: ...
    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]: ...
    def get_asset(self, tenant_id: str, asset_id: str) -> dict[str, Any] | None: ...
    def delete_asset(self, tenant_id: str, asset_id: str) -> bool: ...
    def claim_session(self, tenant_id: str, session_id: str, worker_id: str, now: str, lease_expires_at: str) -> dict[str, Any] | None: ...
    def list_recoverable_sessions(self, now: str, limit: int, tenant_id: str | None = None) -> list[dict[str, Any]]: ...
    def list_sessions_by_state(self, state: str, limit: int, tenant_id: str | None = None) -> list[dict[str, Any]]: ...


class SQLiteIngestionRepository:
    """Single-node durable repository using SQLite and tenant-scoped queries."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._path.is_symlink():
            raise ValueError("SQLite database path must not be a symbolic link")
        if not self._path.exists():
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        if not self._path.is_file():
            raise ValueError("SQLite database path must be a regular file")
        self._path = self._path.resolve(strict=True)
        self._initialize()
        try:
            self._path.chmod(0o600)
        except OSError:
            pass

    def create_or_get_session(self, session: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = self._dump(session)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO ingestion_sessions
                    (session_id, tenant_id, idempotency_key, state, payload_json)
                    VALUES (?, ?, ?, ?, ?)""",
                    (session["session_id"], session["tenant_id"], session["idempotency_key"], session["state"], payload),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT payload_json FROM ingestion_sessions
                    WHERE tenant_id = ? AND idempotency_key = ?""",
                    (session["tenant_id"], session["idempotency_key"]),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise IngestionRepositoryConflict("session identifier already exists") from None
                existing = json.loads(row["payload_json"])
                connection.commit()
                return existing, False
            connection.commit()
            return session, True

    def get_session_by_idempotency(self, tenant_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT payload_json FROM ingestion_sessions
                WHERE tenant_id = ? AND idempotency_key = ?""",
                (tenant_id, idempotency_key),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT payload_json FROM ingestion_sessions
                WHERE tenant_id = ? AND session_id = ?""",
                (tenant_id, session_id),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def update_session(
        self,
        tenant_id: str,
        session_id: str,
        expected_state: str,
        updated_session: dict[str, Any],
    ) -> dict[str, Any]:
        if updated_session.get("tenant_id") != tenant_id:
            raise IngestionRepositoryConflict("updated session tenant does not match repository scope")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE ingestion_sessions SET state = ?, payload_json = ?
                WHERE tenant_id = ? AND session_id = ? AND state = ?""",
                (updated_session["state"], self._dump(updated_session), tenant_id, session_id, expected_state),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise IngestionRepositoryConflict("session state changed concurrently")
            connection.commit()
        return updated_session

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        payload = self._dump(asset)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO audio_assets (asset_id, tenant_id, payload_json) VALUES (?, ?, ?)",
                    (asset["asset_id"], asset["tenant_id"], payload),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT payload_json FROM audio_assets
                    WHERE tenant_id = ? AND asset_id = ?""",
                    (asset["tenant_id"], asset["asset_id"]),
                ).fetchone()
                if row is None or row["payload_json"] != payload:
                    connection.rollback()
                    raise IngestionRepositoryConflict(
                        "asset identifier already exists with different content"
                    ) from None
                connection.commit()
                return json.loads(row["payload_json"])
            connection.commit()
        return asset

    def get_asset(self, tenant_id: str, asset_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT payload_json FROM audio_assets
                WHERE tenant_id = ? AND asset_id = ?""",
                (tenant_id, asset_id),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def delete_asset(self, tenant_id: str, asset_id: str) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM audio_assets WHERE tenant_id = ? AND asset_id = ?",
                (tenant_id, asset_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def claim_session(
        self,
        tenant_id: str,
        session_id: str,
        worker_id: str,
        now: str,
        lease_expires_at: str,
    ) -> dict[str, Any] | None:
        """Atomically claim an active session when its prior lease is absent/expired."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT state, payload_json FROM ingestion_sessions
                WHERE tenant_id = ? AND session_id = ?""",
                (tenant_id, session_id),
            ).fetchone()
            if row is None or row["state"] in {"completed", "failed", "cancelled", "expired"}:
                connection.commit()
                return None

            session = json.loads(row["payload_json"])
            if session.get("tenant_id") != tenant_id:
                connection.rollback()
                raise IngestionRepositoryConflict("session payload tenant does not match repository scope")
            prior_lease = session.get("lease") or {}
            prior_expiry = prior_lease.get("expires_at")
            prior_owner = prior_lease.get("owner_id")
            if prior_expiry and prior_expiry > now and prior_owner != worker_id:
                connection.commit()
                return None

            session["lease"] = {
                "owner_id": worker_id,
                "expires_at": lease_expires_at,
                "heartbeat_at": now,
                "attempt": int(prior_lease.get("attempt", 0)) + 1,
            }
            session["updated_at"] = now
            cursor = connection.execute(
                """UPDATE ingestion_sessions SET payload_json = ?
                WHERE tenant_id = ? AND session_id = ? AND state = ?""",
                (self._dump(session), tenant_id, session_id, row["state"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return session

    def list_recoverable_sessions(
        self,
        now: str,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        states = ("created", "receiving", "finalizing", "queued", "processing")
        placeholders = ",".join("?" for _ in states)
        tenant_clause = " AND tenant_id = ?" if tenant_id is not None else ""
        parameters: tuple[Any, ...] = (*states, now, limit)
        if tenant_id is not None:
            parameters = (*states, tenant_id, now, limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT payload_json FROM ingestion_sessions
                WHERE state IN ({placeholders}){tenant_clause}
                  AND (json_extract(payload_json, '$.lease.expires_at') IS NULL
                       OR json_extract(payload_json, '$.lease.expires_at') <= ?)
                ORDER BY json_extract(payload_json, '$.updated_at') ASC LIMIT ?""",
                parameters,
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def list_sessions_by_state(
        self,
        state: str,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant_clause = " AND tenant_id = ?" if tenant_id is not None else ""
        parameters: tuple[Any, ...] = (state, limit)
        if tenant_id is not None:
            parameters = (state, tenant_id, limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT payload_json FROM ingestion_sessions
                WHERE state = ?{tenant_clause}
                ORDER BY json_extract(payload_json, '$.updated_at') ASC LIMIT ?""",
                parameters,
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_sessions (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (tenant_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_sessions_tenant_state
                    ON ingestion_sessions (tenant_id, state);
                CREATE TABLE IF NOT EXISTS audio_assets (
                    asset_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (tenant_id, asset_id)
                );
                """
            )

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
