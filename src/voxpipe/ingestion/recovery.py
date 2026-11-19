"""Lease recovery and failed-session artifact reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from voxpipe.ingestion.repository import IngestionRepositoryPort
from voxpipe.ingestion.service import (
    IngestionFailed,
    IngestionInProgress,
    IngestionService,
)
from voxpipe.storage.ports import ObjectStoragePort


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

    def run(self, *, limit: int = 100) -> RecoveryReport:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now_text = now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        sessions = self._repository.list_recoverable_sessions(now_text, limit)
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


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    items: tuple[ReconciliationItem, ...]

    @property
    def cleaned(self) -> int:
        return sum(item.error_code is None and (item.asset_deleted or item.object_deleted) for item in self.items)

    @property
    def errors(self) -> int:
        return sum(item.error_code is not None for item in self.items)


class IngestionOrphanReconciler:
    """Cleans session-associated artifacts left by failed compensation.

    This reconciler intentionally scopes cleanup to failed ingestion sessions;
    it never performs a broad object-store prefix deletion.
    """

    def __init__(
        self,
        *,
        repository: IngestionRepositoryPort,
        object_store: ObjectStoragePort,
    ) -> None:
        self._repository = repository
        self._object_store = object_store

    def run(self, *, limit: int = 100) -> ReconciliationReport:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        failed_sessions = self._repository.list_sessions_by_state("failed", limit)
        remaining = max(0, limit - len(failed_sessions))
        expired_sessions = self._repository.list_sessions_by_state("expired", remaining) if remaining else []
        sessions = failed_sessions + expired_sessions
        items: list[ReconciliationItem] = []

        for session in sessions:
            tenant_id = session["tenant_id"]
            asset_id = session["asset_id"]
            object_key = f"tenant/{tenant_id}/assets/{asset_id}/source"
            asset_deleted = False
            object_deleted = False
            error_code: str | None = None

            try:
                asset_deleted = self._repository.delete_asset(tenant_id, asset_id)
            except Exception:
                error_code = "INGESTION_RECONCILE_METADATA_FAILED"

            try:
                object_deleted = self._object_store.delete(object_key)
            except Exception:
                error_code = error_code or "INGESTION_RECONCILE_OBJECT_FAILED"

            items.append(
                ReconciliationItem(
                    session_id=session["session_id"],
                    asset_deleted=asset_deleted,
                    object_deleted=object_deleted,
                    error_code=error_code,
                )
            )

        return ReconciliationReport(tuple(items))
