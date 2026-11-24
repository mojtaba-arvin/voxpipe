"""Durable, tenant-scoped transcription workflow handler.

The workflow journal stores references only. Audio is streamed from object
storage, passed through the provider-neutral STT port, and persisted as an
immutable transcript revision. Stable request idempotency keys and lease
heartbeats make retries/recovery explicit.
"""
from __future__ import annotations

import hashlib
import re
import threading
import tempfile
from dataclasses import replace
from typing import Any, Protocol
from uuid import UUID

from voxpipe.speech.contracts import (
    Transcript,
    TranscriptValidationError,
    TranscriptionError,
    TranscriptionRequest,
    validate_transcript,
)
from voxpipe.speech.observability import SpeechObserver, SpeechToTextService
from voxpipe.speech.ports import SpeechToTextPort
from voxpipe.storage.ports import (
    ObjectNotFoundError,
    ObjectStoragePort,
    StorageError,
    StorageIntegrityError,
    StorageIOError,
)
from voxpipe.workflows.postgres import WorkflowLeaseLost
from voxpipe.workflows.runner import WorkflowHandlerError, WorkflowStepContext


class SpeechAssetTranscriptRepository(Protocol):
    def get_asset(self, tenant_id: str, asset_id: str) -> dict[str, Any] | None: ...
    def create_transcript_revision(self, transcript: dict[str, Any]) -> dict[str, Any]: ...


class _LeaseHeartbeat:
    """Keep long-running local or hosted inference inside its fenced lease."""

    def __init__(self, callback, interval_seconds: float) -> None:
        self._callback = callback
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="voxpipe-stt-workflow-heartbeat", daemon=True,
        )

    def start(self) -> None:
        try:
            self._callback()
        except Exception:
            raise WorkflowLeaseLost("speech workflow lease could not be confirmed") from None
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._callback()
            except Exception:
                self._lost.set()
                return

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            raise WorkflowLeaseLost("speech workflow lease was lost during transcription")

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self._interval * 2))
        if self._thread.is_alive():
            self._lost.set()
        self.ensure_owned()


class SpeechTranscriptionWorkflowHandler:
    """Transcribe one immutable asset and persist a caller-selected revision.

    Required step input references:
      - asset_id: immutable VoxPipe audio asset UUID
      - transcript_id: stable logical transcript UUID
      - revision: positive immutable revision number

    Optional references: language_hint, word_timestamps, diarization.
    No audio bytes, transcript text, credentials or vendor payloads are accepted
    in workflow input/output references.
    """

    _ALLOWED_INPUTS = {
        "asset_id", "transcript_id", "revision", "language_hint",
        "word_timestamps", "diarization",
    }

    def __init__(
        self,
        *,
        repository: SpeechAssetTranscriptRepository,
        object_store: ObjectStoragePort,
        adapter: SpeechToTextPort,
        observer: SpeechObserver | None = None,
        max_audio_bytes: int = 512 * 1024 * 1024,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        if not isinstance(max_audio_bytes, int) or isinstance(max_audio_bytes, bool) or max_audio_bytes < 1:
            raise ValueError("max_audio_bytes must be a positive integer")
        if (not isinstance(heartbeat_interval_seconds, (int, float)) or isinstance(heartbeat_interval_seconds, bool)
                or not 0.05 <= heartbeat_interval_seconds <= 30):
            raise ValueError("heartbeat_interval_seconds must be between 0.05 and 30")
        self._repository = repository
        self._object_store = object_store
        self._adapter = adapter
        self._service = SpeechToTextService(adapter, observer=observer)
        self._max_audio_bytes = max_audio_bytes
        self._heartbeat_interval = float(heartbeat_interval_seconds)

    @staticmethod
    def _retry_failure(context: WorkflowStepContext, code: str, *, retryable: bool, retry_after_seconds: int | None = None) -> WorkflowHandlerError:
        if not retryable:
            return WorkflowHandlerError(code, retryable=False)
        if retry_after_seconds is not None:
            if retry_after_seconds > 3600:
                # Do not retry earlier than a provider's long Retry-After.
                return WorkflowHandlerError(code, retryable=False)
            delay = retry_after_seconds
        else:
            attempt = max(1, int(getattr(context, "attempt", 1)))
            delay = min(300, 2 ** min(attempt - 1, 8))
        return WorkflowHandlerError(code, retryable=True, retry_delay_seconds=delay)

    @staticmethod
    def _uuid(value: Any, field: str) -> str:
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            raise WorkflowHandlerError("STT_INPUT_INVALID", retryable=False) from None

    def __call__(self, context: WorkflowStepContext) -> dict[str, Any]:
        tenant_id = self._uuid(context.tenant_id, "tenant_id")
        refs = context.input_refs
        if not isinstance(refs, dict) or set(refs) - self._ALLOWED_INPUTS:
            raise WorkflowHandlerError("STT_INPUT_INVALID", retryable=False)
        asset_id = self._uuid(refs.get("asset_id"), "asset_id")
        transcript_id = self._uuid(refs.get("transcript_id"), "transcript_id")
        revision = refs.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise WorkflowHandlerError("STT_INPUT_INVALID", retryable=False)
        language_hint = refs.get("language_hint")
        word_timestamps = refs.get("word_timestamps", True)
        diarization = refs.get("diarization", False)
        if not isinstance(word_timestamps, bool) or not isinstance(diarization, bool):
            raise WorkflowHandlerError("STT_INPUT_INVALID", retryable=False)

        try:
            asset = self._repository.get_asset(tenant_id, asset_id)
        except Exception:
            raise self._retry_failure(context, "STT_ASSET_LOOKUP_FAILED", retryable=True) from None
        if not isinstance(asset, dict) or asset.get("asset_id") != asset_id or asset.get("tenant_id") != tenant_id:
            raise WorkflowHandlerError("STT_ASSET_NOT_FOUND", retryable=False)

        media = asset.get("media")
        integrity = asset.get("integrity")
        storage = asset.get("storage")
        if not isinstance(media, dict) or not isinstance(integrity, dict) or not isinstance(storage, dict):
            raise WorkflowHandlerError("STT_ASSET_METADATA_INVALID", retryable=False)
        content_type = media.get("mime_type")
        duration_ms = media.get("duration_ms")
        audio_sha256 = integrity.get("digest")
        object_key = storage.get("object_key")
        if (
            not isinstance(content_type, str) or not content_type.startswith("audio/")
            or not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 1
            or not isinstance(media.get("size_bytes"), int) or isinstance(media.get("size_bytes"), bool) or media.get("size_bytes") < 1
            or not isinstance(audio_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", audio_sha256)
            or not isinstance(object_key, str) or not object_key
        ):
            raise WorkflowHandlerError("STT_ASSET_METADATA_INVALID", retryable=False)

        try:
            info = self._object_store.head(object_key)
        except ObjectNotFoundError:
            raise WorkflowHandlerError("STT_AUDIO_OBJECT_NOT_FOUND", retryable=False) from None
        except StorageIOError:
            raise self._retry_failure(context, "STT_STORAGE_UNAVAILABLE", retryable=True) from None
        except StorageError:
            raise WorkflowHandlerError("STT_STORAGE_FAILURE", retryable=False) from None
        if info.key != object_key or info.sha256 != audio_sha256 or info.size_bytes != media["size_bytes"] or info.size_bytes < 1:
            raise WorkflowHandlerError("STT_AUDIO_INTEGRITY_MISMATCH", retryable=False)
        if info.size_bytes > self._max_audio_bytes:
            raise WorkflowHandlerError("STT_AUDIO_TOO_LARGE", retryable=False)

        idempotency_key = hashlib.sha256(
            f"voxpipe-stt:{context.tenant_id}:{context.workflow_id}:{context.step_id}:{transcript_id}:{revision}".encode()
        ).hexdigest()
        lease_seconds = getattr(context, "lease_seconds", 60)
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 3:
            raise WorkflowHandlerError("STT_LEASE_CONFIGURATION_INVALID", retryable=False)
        heartbeat_interval = min(self._heartbeat_interval, lease_seconds / 3)
        heartbeat = _LeaseHeartbeat(context.heartbeat, heartbeat_interval)
        heartbeat.start()
        try:
            try:
                with self._object_store.open(object_key) as source_stream:
                    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as audio_stream:
                        digest = hashlib.sha256()
                        actual_size = 0
                        while True:
                            chunk = source_stream.read(1024 * 1024)
                            if not isinstance(chunk, bytes):
                                raise WorkflowHandlerError("STT_AUDIO_STREAM_INVALID", retryable=False)
                            if not chunk:
                                break
                            actual_size += len(chunk)
                            if actual_size > self._max_audio_bytes:
                                raise WorkflowHandlerError("STT_AUDIO_TOO_LARGE", retryable=False)
                            digest.update(chunk)
                            audio_stream.write(chunk)
                        if actual_size != info.size_bytes or digest.hexdigest() != audio_sha256:
                            raise WorkflowHandlerError("STT_AUDIO_INTEGRITY_MISMATCH", retryable=False)
                        audio_stream.seek(0)
                        request = TranscriptionRequest(
                            asset_id=asset_id,
                            audio_stream=audio_stream,
                            content_type=content_type,
                            audio_sha256=audio_sha256,
                            language_hint=language_hint,
                            word_timestamps=word_timestamps,
                            diarization=diarization,
                            duration_ms=duration_ms,
                            idempotency_key=idempotency_key,
                        )
                        transcript = self._service.transcribe(request)
            except WorkflowHandlerError:
                raise
            except TranscriptionError as exc:
                raise self._retry_failure(
                    context, exc.code, retryable=exc.retryable,
                    retry_after_seconds=exc.retry_after_seconds,
                ) from None
            except TranscriptValidationError:
                raise WorkflowHandlerError("STT_RESPONSE_INVALID", retryable=False) from None
            except ObjectNotFoundError:
                raise WorkflowHandlerError("STT_AUDIO_OBJECT_NOT_FOUND", retryable=False) from None
            except StorageIOError:
                raise self._retry_failure(context, "STT_STORAGE_UNAVAILABLE", retryable=True) from None
            except StorageError:
                raise WorkflowHandlerError("STT_STORAGE_FAILURE", retryable=False) from None
            except ValueError:
                raise WorkflowHandlerError("STT_REQUEST_INVALID", retryable=False) from None
            except Exception:
                raise WorkflowHandlerError("STT_EXECUTION_FAILED", retryable=False) from None

            heartbeat.ensure_owned()
            stable_transcript = replace(transcript, transcript_id=transcript_id)
            validate_transcript(
                stable_transcript,
                expected_asset_id=asset_id,
                duration_ms=duration_ms,
                expected_audio_sha256=audio_sha256,
            )
            payload = self._persistence_payload(
                context.tenant_id, stable_transcript, revision,
            )
            try:
                self._repository.create_transcript_revision(payload)
            except Exception as exc:
                from voxpipe.ingestion.repository import IngestionRepositoryConflict
                if isinstance(exc, IngestionRepositoryConflict):
                    raise WorkflowHandlerError("STT_REVISION_CONFLICT", retryable=False) from None
                if isinstance(exc, ValueError):
                    raise WorkflowHandlerError("STT_PERSISTENCE_INVALID", retryable=False) from None
                raise self._retry_failure(context, "STT_PERSISTENCE_UNAVAILABLE", retryable=True) from None
            heartbeat.ensure_owned()
            return {
                "asset_id": asset_id,
                "transcript_id": transcript_id,
                "revision": revision,
                "provider": stable_transcript.provenance.provider,
                "model": stable_transcript.provenance.model,
                "language": stable_transcript.language,
                "audio_sha256": audio_sha256,
                "segment_count": len(stable_transcript.segments),
                "word_count": sum(len(segment.words) for segment in stable_transcript.segments),
            }
        finally:
            heartbeat.close()

    @staticmethod
    def _persistence_payload(tenant_id: str, transcript: Transcript, revision: int) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "transcript_id": transcript.transcript_id,
            "asset_id": transcript.asset_id,
            "revision": revision,
            "language": transcript.language,
            "text": transcript.text,
            "provider": transcript.provenance.provider,
            "model": transcript.provenance.model,
            "model_version": transcript.provenance.model_version,
            "audio_sha256": transcript.provenance.audio_sha256,
            "generated_at": transcript.provenance.generated_at.isoformat().replace("+00:00", "Z"),
            "speakers": [
                {
                    "speaker_id": speaker.speaker_id,
                    "label": speaker.label,
                    "confidence": speaker.confidence,
                }
                for speaker in transcript.speakers
            ],
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "confidence": segment.confidence,
                    "speaker_id": segment.speaker_id,
                    "words": [
                        {
                            "word_index": index,
                            "start_ms": word.start_ms,
                            "end_ms": word.end_ms,
                            "text": word.text,
                            "confidence": word.confidence,
                        }
                        for index, word in enumerate(segment.words)
                    ],
                }
                for segment in transcript.segments
            ],
        }
