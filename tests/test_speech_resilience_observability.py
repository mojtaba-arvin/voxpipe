import io
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from voxpipe.speech.circuit_breaker import ProviderCircuitBreaker
from voxpipe.speech.contracts import (
    Transcript, TranscriptProvenance, TranscriptionError,
    TranscriptionErrorCode, TranscriptionRequest,
)
from voxpipe.speech.observability import SpeechOperationEvent, SpeechToTextService
from voxpipe.speech.resilience import CircuitBreakingSpeechToTextPort

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

    def test_retryable_failures_open_the_shared_breaker_and_reject_requests(self):
        clock = [10.0]
        adapter = FakeAdapter([self.error(), self.error()])
        breaker = ProviderCircuitBreaker(
            failure_threshold=2, cooldown_seconds=5, clock=lambda: clock[0],
        )
        guarded = CircuitBreakingSpeechToTextPort(adapter, breaker=breaker)
        for _ in range(2):
            with self.assertRaises(TranscriptionError):
                guarded.transcribe(request())
        self.assertIn("fake-stt", breaker.open_providers())
        with self.assertRaises(TranscriptionError):
            guarded.transcribe(request())
        self.assertEqual(adapter.calls, 2)

    def test_single_half_open_probe_closes_shared_breaker_on_success(self):
        clock = [10.0]
        adapter = FakeAdapter([self.error()])
        breaker = ProviderCircuitBreaker(
            failure_threshold=1, cooldown_seconds=5, clock=lambda: clock[0],
        )
        guarded = CircuitBreakingSpeechToTextPort(adapter, breaker=breaker)
        with self.assertRaises(TranscriptionError):
            guarded.transcribe(request())
        clock[0] += 6
        self.assertEqual(guarded.transcribe(request()).text, "hello")
        self.assertNotIn("fake-stt", breaker.open_providers())

    def test_non_retryable_failure_does_not_open_shared_breaker(self):
        breaker = ProviderCircuitBreaker(failure_threshold=1)
        guarded = CircuitBreakingSpeechToTextPort(
            FakeAdapter([self.error(retryable=False)]), breaker=breaker,
        )
        with self.assertRaises(TranscriptionError):
            guarded.transcribe(request())
        self.assertNotIn("fake-stt", breaker.open_providers())

    def test_half_open_retryable_failure_reopens_shared_breaker(self):
        clock = [1.0]
        adapter = FakeAdapter([self.error(), self.error()])
        breaker = ProviderCircuitBreaker(
            failure_threshold=1, cooldown_seconds=2, clock=lambda: clock[0],
        )
        guarded = CircuitBreakingSpeechToTextPort(adapter, breaker=breaker)
        with self.assertRaises(TranscriptionError):
            guarded.transcribe(request())
        clock[0] += 3
        with self.assertRaises(TranscriptionError):
            guarded.transcribe(request())
        self.assertIn("fake-stt", breaker.open_providers())

    def test_requires_the_shared_breaker_type(self):
        with self.assertRaises(TypeError):
            CircuitBreakingSpeechToTextPort(FakeAdapter(), breaker=object())


if __name__ == "__main__":
    unittest.main()
