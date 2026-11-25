"""Lease recovery and failed-session artifact reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from voxpipe.ingestion.repository import IngestionRepositoryPort
from voxpipe.ingestion.service import (
    IngestionFailed,
    IngestionInProgress,
    IngestionService,
)
from voxpipe.storage.ports import ObjectStoragePort
from voxpipe.storage.staging import remove_staged_source


@dataclass(frozen=True, slots=True)
class RecoveryItem:
    session_id: str
    outcome: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    items: tuple[RecoveryItem, ...]

    @property
    def recovered(self) -> int:
        return sum(item.outcome == "recovered" for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.outcome == "failed" for item in self.items)

    @property
    def skipped(self) -> int:
        return sum(item.outcome == "skipped" for item in self.items)


class IngestionRecoveryWorker:
    """Resumes sessions whose worker lease is absent or has expired."""

    def __init__(
        self,
        *,
        repository: IngestionRepositoryPort,
        service: IngestionService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._service = service
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, *, tenant_id: str | None = None, limit: int = 100) -> RecoveryReport:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now_text = now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        sessions = self._repository.list_recoverable_sessions(now_text, limit, tenant_id=tenant_id)
        items: list[RecoveryItem] = []

        for session in sessions:
            session_id = session["session_id"]
            try:
                self._service.resume_session(session["tenant_id"], session_id)
            except IngestionInProgress:
                items.append(RecoveryItem(session_id, "skipped"))
            except IngestionFailed as error:
                items.append(RecoveryItem(session_id, "failed", error.code))
            except Exception:
                items.append(RecoveryItem(session_id, "failed", "INGESTION_RECOVERY_ERROR"))
            else:
                items.append(RecoveryItem(session_id, "recovered"))

        return RecoveryReport(tuple(items))


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    session_id: str
    asset_deleted: bool
    object_deleted: bool
    error_code: str | None = None
    staging_deleted: bool = False


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    items: tuple[ReconciliationItem, ...]
    retention_deleted: int = 0
    retention_errors: int = 0

    @property
    def cleaned(self) -> int:
        return sum(item.error_code is None and (item.asset_deleted or item.object_deleted or item.staging_deleted) for item in self.items) + self.retention_deleted

    @property
    def errors(self) -> int:
        return sum(item.error_code is not None for item in self.items) + self.retention_errors


class IngestionOrphanReconciler:
    """Reconcile terminal session artifacts and server-assigned asset retention."""

    def __init__(
        self,
        *,
        repository: IngestionRepositoryPort,
        object_store: ObjectStoragePort,
        staging_root: str | Path | None,
        storage_provider: str = "local",
    ) -> None:
        if storage_provider not in {"local", "s3_compatible"}:
            raise ValueError("unsupported storage provider")
        self._repository = repository
        self._object_store = object_store
        self._storage_provider = storage_provider
        self._staging_root = Path(staging_root).expanduser() if staging_root is not None else None

    def run(self, *, tenant_id: str | None = None, limit: int = 100) -> ReconciliationReport:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expired_candidates = self._repository.list_expired_sessions(now_text, limit, tenant_id)
        for candidate in expired_candidates:
            expired = dict(candidate)
            prior_state = expired["state"]
            expired["state"] = "expired"
            expired.pop("lease", None)
            expired["updated_at"] = now_text
            try:
                self._repository.update_session(
                    expired["tenant_id"], expired["session_id"], prior_state, expired
                )
                self._repository.record_audit_event(
                    expired["tenant_id"], expired["session_id"], expired.get("asset_id"),
                    "ingestion.expired", "succeeded", "INGESTION_SESSION_EXPIRED",
                    {"prior_state": prior_state},
                )
            except Exception:
                continue

        sessions: list[dict] = []
        seen: set[str] = set()
        for state in ("failed", "expired", "cancelled", "completed"):
            remaining = limit - len(sessions)
            if remaining <= 0:
                break
            for session in self._repository.list_sessions_by_state(state, remaining, tenant_id=tenant_id):
                if session["session_id"] not in seen:
                    seen.add(session["session_id"])
                    sessions.append(session)

        items: list[ReconciliationItem] = []
        for session in sessions:
            tenant = session["tenant_id"]
            session_id = session["session_id"]
            asset_id = session["asset_id"]
            completed = session["state"] == "completed"
            object_key = f"tenant/{tenant}/assets/{asset_id}/source"
            asset_deleted = False
            object_deleted = False
            staging_deleted = False
            error_code: str | None = None

            try:
                reference = session.get("staging_reference")
                if reference:
                    staging_backend = session.get("staging_backend", "local")
                    if staging_backend == "local":
                        if self._staging_root is None:
                            raise ValueError("local staging root is not configured")
                        staging_deleted = remove_staged_source(
                            self._staging_root, tenant, session_id, reference,
                            include_temporary=True,
                        )
                    elif staging_backend == "s3_compatible":
                        expected_reference = f"staging/v1/{tenant}/{session_id}/source"
                        if self._storage_provider != "s3_compatible" or reference != expected_reference:
                            raise ValueError("S3 staging reference does not match the session identity")
                        staging_deleted = self._object_store.delete(reference)
                    else:
                        raise ValueError("unsupported staging backend")
            except Exception:
                error_code = "INGESTION_RECONCILE_STAGING_FAILED"

            if not completed:
                try:
                    existing_asset = self._repository.get_asset(tenant, asset_id)
                    if existing_asset:
                        object_key = (existing_asset.get("storage") or {}).get("object_key") or object_key
                except Exception:
                    error_code = error_code or "INGESTION_RECONCILE_METADATA_FAILED"
                if error_code is None:
                    try:
                        object_deleted = self._object_store.delete(object_key)
                    except Exception:
                        error_code = "INGESTION_RECONCILE_OBJECT_FAILED"
                if error_code is None:
                    try:
                        asset_deleted = self._repository.delete_asset(tenant, asset_id)
                    except Exception:
                        error_code = "INGESTION_RECONCILE_METADATA_FAILED"

            try:
                self._repository.record_audit_event(
                    tenant, session_id, asset_id, "ingestion.cleanup",
                    "failed" if error_code else "succeeded", error_code,
                    {"staging_deleted": staging_deleted, "asset_deleted": asset_deleted,
                     "object_deleted": object_deleted},
                )
            except Exception:
                error_code = error_code or "INGESTION_AUDIT_WRITE_FAILED"

            if error_code is None:
                cleaned = dict(session)
                cleaned["staging_cleanup_completed_at"] = now_text
                cleaned["updated_at"] = now_text
                try:
                    self._repository.update_session(tenant, session_id, session["state"], cleaned)
                except Exception:
                    error_code = "INGESTION_CLEANUP_STATE_UPDATE_FAILED"

            items.append(ReconciliationItem(
                session_id=session_id, asset_deleted=asset_deleted,
                object_deleted=object_deleted, error_code=error_code,
                staging_deleted=staging_deleted,
            ))

        retention_deleted = 0
        retention_errors = 0
        for asset in self._repository.list_expired_assets(now_text, limit, tenant_id=tenant_id):
            tenant = asset["tenant_id"]
            asset_id = asset["asset_id"]
            object_key = (asset.get("storage") or {}).get("object_key")
            try:
                if object_key:
                    self._object_store.delete(object_key)
                self._repository.delete_asset(tenant, asset_id)
                self._repository.record_audit_event(
                    tenant, asset.get("session_id"), asset_id,
                    "asset.retention_deleted", "succeeded", None,
                    {"retention_expires_at": asset.get("retention_expires_at")},
                )
                retention_deleted += 1
            except Exception:
                retention_errors += 1
                try:
                    self._repository.record_audit_event(
                        tenant, asset.get("session_id"), asset_id,
                        "asset.retention_deleted", "failed",
                        "ASSET_RETENTION_DELETE_FAILED", {},
                    )
                except Exception:
                    pass

        return ReconciliationReport(tuple(items), retention_deleted, retention_errors)
