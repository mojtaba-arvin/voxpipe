"""Environment-driven composition for a supervised local speech workflow worker."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

from voxpipe.ingestion.postgres import PostgresIngestionRepository
from voxpipe.speech.faster_whisper import FasterWhisperAdapter
from voxpipe.speech.runtime import build_speech_resilience_runtime
from voxpipe.storage.local import LocalFilesystemObjectStore
from voxpipe.workflows.postgres import PostgresWorkflowRepository
from voxpipe.workflows.runner import PostgresWorkflowWorker, WorkflowHandlerRegistry
from voxpipe.workflows.speech import SpeechTranscriptionWorkflowHandler


@dataclass(frozen=True, slots=True)
class WorkflowWorkerRuntime:
    worker: PostgresWorkflowWorker
    tenant_id: str
    poll_interval_seconds: float

    def run_forever(self, stop_event) -> None:
        self.worker.run_forever(
            self.tenant_id, stop_event,
            poll_interval_seconds=self.poll_interval_seconds,
        )


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _integer(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(key, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _positive_float(env: Mapping[str, str], key: str, default: float, maximum: float) -> float:
    raw = env.get(key, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{key} must be greater than zero and at most {maximum}")
    return value


def build_local_speech_worker(env: Mapping[str, str] | None = None) -> WorkflowWorkerRuntime:
    """Build only the allowlisted local speech handler; never enable hosted STT."""
    settings = os.environ if env is None else env
    dsn = _required(settings, "VOXPIPE_WORKFLOW_DATABASE_DSN")
    tenant_id = _required(settings, "VOXPIPE_WORKFLOW_TENANT_ID")
    try:
        tenant_id = str(UUID(tenant_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("VOXPIPE_WORKFLOW_TENANT_ID must be a UUID") from None

    object_root = Path(_required(settings, "VOXPIPE_OBJECT_STORE_ROOT")).expanduser()
    worker_id = settings.get("VOXPIPE_WORKFLOW_WORKER_ID", "").strip() or str(uuid4())
    try:
        worker_id = str(UUID(worker_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("VOXPIPE_WORKFLOW_WORKER_ID must be a UUID") from None

    lease_seconds = _integer(settings, "VOXPIPE_WORKFLOW_LEASE_SECONDS", 60, 3, 3600)
    scan_limit = _integer(settings, "VOXPIPE_WORKFLOW_SCAN_LIMIT", 100, 1, 1000)
    poll_interval = _positive_float(settings, "VOXPIPE_WORKFLOW_POLL_INTERVAL_SECONDS", 1.0, 60.0)
    max_audio_bytes = _integer(
        settings, "VOXPIPE_WORKFLOW_MAX_AUDIO_BYTES", 512 * 1024 * 1024, 1, 2 * 1024 * 1024 * 1024,
    )

    workflow_repository = PostgresWorkflowRepository(dsn)
    asset_repository = PostgresIngestionRepository(dsn)
    object_store = LocalFilesystemObjectStore(object_root, max_object_size_bytes=max_audio_bytes)
    adapter = FasterWhisperAdapter(
        model_name=settings.get("VOXPIPE_STT_MODEL_NAME", "small").strip() or "small",
        device=settings.get("VOXPIPE_STT_DEVICE", "cpu").strip() or "cpu",
        compute_type=settings.get("VOXPIPE_STT_COMPUTE_TYPE", "int8").strip() or "int8",
        download_root=settings.get("VOXPIPE_STT_MODEL_CACHE") or None,
        local_files_only=True,
        max_audio_bytes=max_audio_bytes,
    )
    resilience = build_speech_resilience_runtime(adapter, environ=settings)
    handler = SpeechTranscriptionWorkflowHandler(
        repository=asset_repository,
        object_store=object_store,
        adapter=resilience.speech_port,
        max_audio_bytes=max_audio_bytes,
    )
    registry = WorkflowHandlerRegistry({"speech.transcribe": handler})
    worker = PostgresWorkflowWorker(
        workflow_repository, registry, worker_id=worker_id,
        lease_seconds=lease_seconds, scan_limit=scan_limit,
    )
    return WorkflowWorkerRuntime(worker, tenant_id, poll_interval)
