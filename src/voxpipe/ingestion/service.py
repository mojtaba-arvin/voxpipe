"""Synchronous batch ingestion application service."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from voxpipe.media.probe import FFprobeMediaInspector, MediaInspectionError, MediaMetadata
from voxpipe.media.validation import FFmpegDecodeValidator
from voxpipe.storage.ports import ObjectStoragePort, StorageError
from voxpipe.ingestion.repository import (
    IngestionRepositoryConflict,
    IngestionRepositoryPort,
)


_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_SOURCE_KINDS = {"file_upload", "device_capture", "telephony", "live_stream", "import"}
_STORAGE_PROVIDERS = {"s3_compatible", "gcs", "azure_blob", "local"}
_ALLOWED_SOURCE_FIELDS = {"kind", "external_reference", "recorded_at", "declared_by"}

_ALLOWED_TRANSITIONS = {
    "created": {"receiving", "failed", "cancelled", "expired"},
    "receiving": {"finalizing", "failed", "cancelled", "expired"},
    "finalizing": {"queued", "failed", "cancelled", "expired"},
    "queued": {"processing", "failed", "cancelled", "expired"},
    "processing": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "expired": set(),
}


class IngestionRequestError(Exception):
    code = "INGESTION_INVALID_REQUEST"


class IngestionInProgress(Exception):
    code = "INGESTION_IN_PROGRESS"

    def __init__(self, session_id: str) -> None:
        super().__init__("An ingestion session with this idempotency key is already active")
        self.session_id = session_id


class IngestionIdempotencyConflict(Exception):
    code = "INGESTION_IDEMPOTENCY_CONFLICT"


class IngestionFailed(Exception):
    def __init__(self, code: str, session: dict[str, Any] | None = None) -> None:
        super().__init__(f"Ingestion failed: {code}")
        self.code = code
        self.session = session


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    tenant_id: str
    idempotency_key: str
    staged_path: str | Path
    source: Mapping[str, Any]
    retention_policy_id: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    session: dict[str, Any]
    asset: dict[str, Any]


class IngestionService:
    """Coordinates staged media validation, immutable storage, and metadata.

    The caller must supply tenant identity from authenticated context and a
    staged file path beneath the service-owned staging root. This service does
    not accept upload URLs or client-controlled filesystem paths.
    """

    def __init__(
        self,
        *,
        staging_root: str | Path,
        repository: IngestionRepositoryPort,
        object_store: ObjectStoragePort,
        media_inspector: FFprobeMediaInspector | None = None,
        decode_validator: FFmpegDecodeValidator | None = None,
        storage_provider: str = "local",
        max_file_size_bytes: int = 512 * 1024 * 1024,
        session_ttl: timedelta = timedelta(hours=24),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if storage_provider not in _STORAGE_PROVIDERS:
            raise ValueError("unsupported storage provider")
        if max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes must be greater than zero")
        if session_ttl.total_seconds() <= 0:
            raise ValueError("session_ttl must be greater than zero")

        self._staging_root = Path(staging_root).expanduser()
        self._staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._staging_root = self._staging_root.resolve(strict=True)
        if not self._staging_root.is_dir():
            raise ValueError("staging_root must be a directory")

        self._repository = repository
        self._object_store = object_store
        self._media_inspector = media_inspector or FFprobeMediaInspector()
        self._decode_validator = decode_validator or FFmpegDecodeValidator()
        self._storage_provider = storage_provider
        self._max_file_size_bytes = max_file_size_bytes
        self._session_ttl = session_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def ingest(self, request: IngestionRequest) -> IngestionResult:
        tenant_id = self._tenant_id(request.tenant_id)
        idempotency_key = self._idempotency_key(request.idempotency_key)
        source = self._source(request.source)
        staged_path, size_bytes = self._staged_file(request.staged_path)
        digest = self._sha256_file(staged_path)

        now = self._now()
        session_id = str(uuid4())
        asset_id = str(uuid4())
        timestamp = self._format_time(now)
        session = {
            "schema_version": "1.0.0",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "mode": "batch",
            "source": source,
            "state": "created",
            "idempotency_key": idempotency_key,
            "asset_id": asset_id,
            "upload": {
                "expected_size_bytes": size_bytes,
                "received_size_bytes": size_bytes,
                "expected_sha256": digest,
            },
            "created_at": timestamp,
            "updated_at": timestamp,
            "expires_at": self._format_time(now + self._session_ttl),
        }

        current, created = self._repository.create_or_get_session(session)
        if not created:
            existing_upload = current.get("upload", {})
            if (
                existing_upload.get("expected_sha256") != digest
                or existing_upload.get("expected_size_bytes") != size_bytes
                or current.get("source") != source
            ):
                raise IngestionIdempotencyConflict(
                    "idempotency key was already used for a different request"
                )
            if current["state"] == "completed":
                asset = self._repository.get_asset(tenant_id, current["asset_id"])
                if asset is None:
                    raise IngestionFailed("INGESTION_ASSET_MISSING", current)
                return IngestionResult(session=current, asset=asset)
            if current["state"] == "failed":
                failure = current.get("failure", {})
                raise IngestionFailed(failure.get("code", "INGESTION_FAILED"), current)
            if current["state"] in {"cancelled", "expired"}:
                raise IngestionFailed("INGESTION_SESSION_TERMINAL", current)
            raise IngestionInProgress(current["session_id"])

        object_key = f"tenant/{tenant_id}/assets/{asset_id}/source"
        asset_created = False
        try:
            current = self._transition(current, "receiving")
            current = self._transition(current, "finalizing")

            metadata = self._media_inspector.inspect(staged_path)
            self._decode_validator.validate(staged_path)

            current = self._transition(current, "queued")
            current = self._transition(current, "processing")

            with staged_path.open("rb") as source_stream:
                stored = self._object_store.put(
                    object_key,
                    source_stream,
                    expected_sha256=digest,
                )

            asset = self._build_asset(
                tenant_id=tenant_id,
                asset_id=asset_id,
                source=source,
                metadata=metadata,
                object_key=object_key,
                checksum=stored.sha256,
                retention_policy_id=request.retention_policy_id,
            )
            asset = self._repository.create_asset(asset)
            asset_created = True

            current = self._transition(current, "completed")
            return IngestionResult(session=current, asset=asset)
        except Exception as error:
            cleanup_failed = False
            if asset_created:
                try:
                    self._repository.delete_asset(tenant_id, asset_id)
                except Exception:
                    cleanup_failed = True
            try:
                self._object_store.delete(object_key)
            except Exception:
                cleanup_failed = True

            failure_code = "INGESTION_CLEANUP_FAILED" if cleanup_failed else self._error_code(error)
            latest = self._repository.get_session(tenant_id, session_id) or current
            if latest["state"] not in {"completed", "failed", "cancelled", "expired"}:
                failed = dict(latest)
                failed["state"] = "failed"
                failed["updated_at"] = self._format_time(self._now())
                failed["failure"] = {
                    "code": failure_code,
                    "retryable": self._is_retryable(failure_code),
                    "detail": "Processing failed; inspect restricted service diagnostics.",
                }
                try:
                    latest = self._repository.update_session(
                        tenant_id,
                        session_id,
                        latest["state"],
                        failed,
                    )
                except IngestionRepositoryConflict:
                    latest = self._repository.get_session(tenant_id, session_id) or latest
            raise IngestionFailed(failure_code, latest) from error

    def _transition(self, session: dict[str, Any], target: str) -> dict[str, Any]:
        current_state = session["state"]
        if target not in _ALLOWED_TRANSITIONS.get(current_state, set()):
            raise IngestionRepositoryConflict(
                f"invalid ingestion state transition: {current_state} -> {target}"
            )
        updated = dict(session)
        updated["state"] = target
        updated["updated_at"] = self._format_time(self._now())
        return self._repository.update_session(
            session["tenant_id"],
            session["session_id"],
            current_state,
            updated,
        )

    def _staged_file(self, file_path: str | Path) -> tuple[Path, int]:
        source = Path(file_path)
        if source.is_symlink():
            raise IngestionRequestError("staged media must not be a symbolic link")
        try:
            resolved = source.resolve(strict=True)
        except (OSError, RuntimeError):
            raise IngestionRequestError("staged media file does not exist") from None
        if not resolved.is_relative_to(self._staging_root):
            raise IngestionRequestError("staged media path is outside the private staging root")
        if not resolved.is_file():
            raise IngestionRequestError("staged media must be a regular file")
        size_bytes = resolved.stat().st_size
        if size_bytes < 1:
            raise IngestionRequestError("staged media file is empty")
        if size_bytes > self._max_file_size_bytes:
            raise IngestionRequestError("staged media file exceeds the configured size limit")
        return resolved, size_bytes

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _tenant_id(value: str) -> str:
        try:
            return str(UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise IngestionRequestError("tenant_id must be a UUID from authenticated context") from None

    @staticmethod
    def _idempotency_key(value: str) -> str:
        if not isinstance(value, str) or not _IDEMPOTENCY_RE.fullmatch(value):
            raise IngestionRequestError("idempotency_key must contain 16 to 128 safe characters")
        return value

    @staticmethod
    def _source(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise IngestionRequestError("source metadata must be an object")
        source = dict(value)
        if set(source) - _ALLOWED_SOURCE_FIELDS:
            raise IngestionRequestError("source metadata contains unsupported fields")
        if source.get("kind") not in _SOURCE_KINDS:
            raise IngestionRequestError("source.kind is not supported")
        for field_name, maximum in (("external_reference", 2048), ("declared_by", 256)):
            field_value = source.get(field_name)
            if field_value is not None and (
                not isinstance(field_value, str) or not field_value or len(field_value) > maximum
            ):
                raise IngestionRequestError(f"source.{field_name} is invalid")
        recorded_at = source.get("recorded_at")
        if recorded_at is not None:
            if not isinstance(recorded_at, str):
                raise IngestionRequestError("source.recorded_at must be a date-time string")
            try:
                parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
            except ValueError:
                raise IngestionRequestError("source.recorded_at must be an ISO 8601 date-time") from None
            if parsed.tzinfo is None:
                raise IngestionRequestError("source.recorded_at must include a timezone")
        return source

    def _build_asset(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        source: dict[str, Any],
        metadata: MediaMetadata,
        object_key: str,
        checksum: str,
        retention_policy_id: str | None,
    ) -> dict[str, Any]:
        timestamp = self._format_time(self._now())
        media: dict[str, Any] = {
            "mime_type": metadata.mime_type,
            "container_format": metadata.container_format,
            "codec": metadata.codec,
            "duration_ms": metadata.duration_ms,
            "size_bytes": metadata.size_bytes,
            "sample_rate_hz": metadata.sample_rate_hz,
            "channels": metadata.channels,
            "audio_track_index": metadata.audio_track_index,
        }
        if metadata.bit_depth is not None:
            media["bit_depth"] = metadata.bit_depth
        if metadata.channel_layout is not None:
            media["channel_layout"] = metadata.channel_layout

        asset: dict[str, Any] = {
            "schema_version": "1.0.0",
            "asset_id": asset_id,
            "tenant_id": tenant_id,
            "source": source,
            "media": media,
            "integrity": {"algorithm": "sha256", "digest": checksum},
            "storage": {"provider": self._storage_provider, "object_key": object_key},
            "state": "accepted",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if retention_policy_id is not None:
            asset["retention_policy_id"] = retention_policy_id
        return asset

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, (MediaInspectionError, StorageError)):
            return error.code
        if isinstance(error, IngestionRepositoryConflict):
            return "INGESTION_STATE_CONFLICT"
        if isinstance(error, OSError):
            return "INGESTION_IO_ERROR"
        return "INGESTION_INTERNAL_ERROR"

    @staticmethod
    def _is_retryable(code: str) -> bool:
        return code.endswith("_TIMEOUT") or code in {
            "STORAGE_IO_ERROR",
            "MEDIA_PROBE_UNAVAILABLE",
            "MEDIA_DECODER_UNAVAILABLE",
            "INGESTION_IO_ERROR",
        }
