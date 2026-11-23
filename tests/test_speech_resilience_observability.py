import io
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from voxpipe.speech.contracts import (
    Transcript, TranscriptProvenance, TranscriptionError,
    TranscriptionErrorCode, TranscriptionRequest,
)
from voxpipe.speech.observability import SpeechOperationEvent, SpeechToTextService
from voxpipe.speech.resilience import CircuitBreakingSpeechToTextPort, CircuitState

DIGEST = "a" * 64


def request():
    return TranscriptionRequest(
        asset_id=str(uuid4()), audio_stream=io.BytesIO(b"audio"),
        content_type="audio/wav", audio_sha256=DIGEST, duration_ms=1000,
    )


class FakeAdapter:
    provider_name = "fake-stt"

    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = 0

    def transcribe(self, req):
        self.calls += 1
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        now = datetime.now(timezone.utc)
        return Transcript(
            transcript_id=str(uuid4()), asset_id=req.asset_id, language="en",
            text="hello", segments=(), speakers=(),
            provenance=TranscriptProvenance(
                provider=self.provider_name, model="test", audio_sha256=DIGEST,
                generated_at=now,
            ), created_at=now,
        )


class Recorder:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


class SpeechReliabilityTests(unittest.TestCase):
    def error(self, retryable=True):
        return TranscriptionError(
            TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
            provider="fake-stt", retryable=retryable,
        )

    def test_service_emits_started_and_success_without_sensitive_fields(self):
        adapter, recorder = FakeAdapter(), Recorder()
        service = SpeechToTextService(adapter, observer=recorder)
        result = service.transcribe(request())
        self.assertEqual(result.text, "hello")
        self.assertEqual([e.event for e in recorder.events], ["started", "succeeded"])
        self.assertTrue(all(isinstance(e, SpeechOperationEvent) for e in recorder.events))
        self.assertFalse(hasattr(recorder.events[0], "asset_id"))
        self.assertFalse(hasattr(recorder.events[0], "transcript"))

    def test_service_records_stable_failure_code(self):
        recorder = Recorder()
        service = SpeechToTextService(FakeAdapter([self.error()]), observer=recorder)
        with self.assertRaises(TranscriptionError):
            service.transcribe(request())
        self.assertEqual(recorder.events[-1].error_code, TranscriptionErrorCode.PROVIDER_UNAVAILABLE)
        self.assertTrue(recorder.events[-1].retryable)

    def test_observer_failure_does_not_break_transcription(self):
        class BrokenObserver:
            def record(self, event):
                raise RuntimeError("telemetry down")
        self.assertEqual(SpeechToTextService(FakeAdapter(), observer=BrokenObserver()).transcribe(request()).text, "hello")

    def test_circuit_opens_after_retryable_failure_threshold(self):
        clock = [10.0]
        adapter = FakeAdapter([self.error(), self.error()])
        breaker = CircuitBreakingSpeechToTextPort(
            adapter, failure_threshold=2, recovery_timeout_seconds=5,
            clock=lambda: clock[0],
        )
        for _ in range(2):
            with self.assertRaises(TranscriptionError):
                breaker.transcribe(request())
        self.assertEqual(breaker.snapshot().state, CircuitState.OPEN)
        with self.assertRaises(TranscriptionError):
            breaker.transcribe(request())
        self.assertEqual(adapter.calls, 2)

    def test_circuit_allows_single_half_open_probe_and_closes_on_success(self):
        clock = [10.0]
        adapter = FakeAdapter([self.error()])
        breaker = CircuitBreakingSpeechToTextPort(
            adapter, failure_threshold=1, recovery_timeout_seconds=5,
            clock=lambda: clock[0],
        )
        with self.assertRaises(TranscriptionError):
            breaker.transcribe(request())
        clock[0] += 6
        self.assertEqual(breaker.snapshot().state, CircuitState.HALF_OPEN)
        self.assertEqual(breaker.transcribe(request()).text, "hello")
        self.assertEqual(breaker.snapshot().state, CircuitState.CLOSED)

    def test_non_retryable_failure_does_not_open_circuit(self):
        breaker = CircuitBreakingSpeechToTextPort(
            FakeAdapter([self.error(retryable=False)]), failure_threshold=1,
        )
        with self.assertRaises(TranscriptionError):
            breaker.transcribe(request())
        self.assertEqual(breaker.snapshot().state, CircuitState.CLOSED)

    def test_half_open_probe_failure_reopens_circuit(self):
        clock = [1.0]
        adapter = FakeAdapter([self.error(), self.error()])
        breaker = CircuitBreakingSpeechToTextPort(
            adapter, failure_threshold=1, recovery_timeout_seconds=2,
            clock=lambda: clock[0],
        )
        with self.assertRaises(TranscriptionError):
            breaker.transcribe(request())
        clock[0] += 3
        with self.assertRaises(TranscriptionError):
            breaker.transcribe(request())
        self.assertEqual(breaker.snapshot().state, CircuitState.OPEN)

    def test_configuration_validation(self):
        with self.assertRaises(ValueError):
            CircuitBreakingSpeechToTextPort(FakeAdapter(), failure_threshold=0)
        with self.assertRaises(ValueError):
            CircuitBreakingSpeechToTextPort(FakeAdapter(), recovery_timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
