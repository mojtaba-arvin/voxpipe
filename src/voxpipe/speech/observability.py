"""Privacy-safe observability for speech-to-text operations.

Observers receive bounded metadata only. Audio, transcript text, credentials,
tenant IDs, asset IDs, provider payloads and local paths never enter events.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from voxpipe.speech.contracts import (
    Transcript,
    TranscriptValidationError,
    TranscriptionError,
    TranscriptionRequest,
    validate_transcript,
)
from voxpipe.speech.ports import SpeechToTextPort


@dataclass(frozen=True, slots=True)
class SpeechOperationEvent:
    """One lifecycle event with bounded, content-free dimensions."""

    operation_id: str
    event: str
    provider: str
    duration_ms: int | None = None
    error_code: str | None = None
    retryable: bool | None = None


class SpeechObserver(Protocol):
    """Adapter to OpenTelemetry, metrics, logs or a test recorder."""

    def record(self, event: SpeechOperationEvent) -> None: ...


class NoopSpeechObserver:
    def record(self, event: SpeechOperationEvent) -> None:
        return None


class SpeechToTextService:
    """Validate results and emit best-effort lifecycle telemetry.

    Observer failures are deliberately isolated from transcription. The event
    schema is suitable for metrics/traces and avoids high-cardinality or
    sensitive labels.
    """

    def __init__(
        self,
        adapter: SpeechToTextPort,
        *,
        observer: SpeechObserver | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter = adapter
        self._observer = observer or NoopSpeechObserver()
        self._clock = clock

    def _record(self, event: SpeechOperationEvent) -> None:
        try:
            self._observer.record(event)
        except Exception:
            # Telemetry must never turn a valid transcription into a failure.
            return None

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        operation_id = str(uuid.uuid4())
        provider = self._adapter.provider_name
        started = self._clock()
        self._record(SpeechOperationEvent(operation_id, "started", provider))
        try:
            result = self._adapter.transcribe(request)
            if result.provenance.provider != provider:
                raise TranscriptValidationError(
                    "transcript provider provenance does not match the selected adapter"
                )
            validate_transcript(
                result,
                expected_asset_id=request.asset_id,
                duration_ms=request.duration_ms,
                expected_audio_sha256=request.audio_sha256,
            )
        except TranscriptionError as exc:
            self._record(SpeechOperationEvent(
                operation_id, "failed", provider,
                max(0, round((self._clock() - started) * 1000)),
                exc.code, exc.retryable,
            ))
            raise
        except Exception:
            self._record(SpeechOperationEvent(
                operation_id, "failed", provider,
                max(0, round((self._clock() - started) * 1000)),
                "STT_INTERNAL_ERROR", False,
            ))
            raise
        self._record(SpeechOperationEvent(
            operation_id, "succeeded", provider,
            max(0, round((self._clock() - started) * 1000)),
        ))
        return result


class OpenTelemetrySpeechObserver:
    """Bridge lifecycle events to injected OpenTelemetry meter and tracer.

    The observer accepts API objects instead of configuring global providers;
    applications retain control over exporters, sampling, resource attributes
    and shutdown. Metric attributes are deliberately bounded and content-free.
    """

    def __init__(self, *, meter, tracer) -> None:
        self._counter = meter.create_counter(
            "voxpipe.stt.operations", unit="{operation}",
            description="Speech-to-text operation lifecycle events",
        )
        self._duration = meter.create_histogram(
            "voxpipe.stt.duration", unit="ms",
            description="Speech-to-text operation duration",
        )
        self._tracer = tracer
        self._spans = {}
        import threading
        self._lock = threading.Lock()

    def record(self, event: SpeechOperationEvent) -> None:
        attributes = {"provider": event.provider, "outcome": event.event}
        if event.error_code is not None:
            attributes["error_code"] = event.error_code
        if event.retryable is not None:
            attributes["retryable"] = event.retryable
        self._counter.add(1, attributes)
        if event.event == "started":
            span = self._tracer.start_span(
                "voxpipe.stt.transcribe",
                attributes={"stt.provider": event.provider},
            )
            with self._lock:
                self._spans[event.operation_id] = span
            return
        if event.duration_ms is not None:
            self._duration.record(event.duration_ms, attributes)
        with self._lock:
            span = self._spans.pop(event.operation_id, None)
        if span is not None:
            span.set_attribute("stt.outcome", event.event)
            if event.error_code is not None:
                span.set_attribute("stt.error_code", event.error_code)
            if event.retryable is not None:
                span.set_attribute("stt.retryable", event.retryable)
            if event.event == "failed":
                span.set_attribute("error", True)
            span.end()
