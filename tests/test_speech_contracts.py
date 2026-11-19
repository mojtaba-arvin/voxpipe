"""Provider-neutral speech contract and adapter boundary tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO

from voxpipe.speech import (
    SpeechToTextService,
    Transcript,
    TranscriptProvenance,
    TranscriptSegment,
    TranscriptSpeaker,
    TranscriptValidationError,
    TranscriptWord,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    validate_transcript,
)

ASSET_ID = "9d6eaa44-70c4-4d42-91d8-47d4d4c6f002"
SPEAKER_ID = "b1e8c0d7-7e6f-4e28-9f53-6d2e2a7f0202"


def valid_transcript() -> Transcript:
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    return Transcript(
        transcript_id="8e45d7a1-9b7b-4c43-a2b4-4d88efc4c101",
        asset_id=ASSET_ID,
        language="fa-IR",
        text="سلام، جلسه را شروع می‌کنیم.",
        segments=(
            TranscriptSegment(
                segment_id="a0f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0101",
                start_ms=120,
                end_ms=1820,
                text="سلام، جلسه را شروع می‌کنیم.",
                confidence=0.96,
                speaker_id=SPEAKER_ID,
                words=(
                    TranscriptWord(120, 510, "سلام،", 0.98),
                    TranscriptWord(540, 890, "جلسه", 0.95),
                    TranscriptWord(900, 1040, "را", 0.96),
                    TranscriptWord(1060, 1820, "شروع می‌کنیم.", 0.94),
                ),
            ),
        ),
        speakers=(TranscriptSpeaker(SPEAKER_ID, "speaker_1", 0.91),),
        provenance=TranscriptProvenance(
            provider="fixture",
            model="deterministic-test",
            model_version="1",
            audio_sha256="a" * 64,
            generated_at=now,
        ),
        created_at=now,
    )


class FakeSpeechToTextAdapter:
    provider_name = "fixture"

    def __init__(self, result: Transcript) -> None:
        self.result = result
        self.requests: list[TranscriptionRequest] = []

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        self.requests.append(request)
        return self.result


class SpeechContractTests(unittest.TestCase):
    def test_valid_transcript_passes_cross_field_validation(self) -> None:
        validate_transcript(valid_transcript(), expected_asset_id=ASSET_ID, duration_ms=2000)

    def test_request_accepts_stream_and_declared_audio_capabilities(self) -> None:
        request = TranscriptionRequest(
            asset_id=ASSET_ID,
            audio_stream=BytesIO(b"audio"),
            content_type="audio/wav",
            language_hint="fa-IR",
            word_timestamps=True,
            diarization=True,
            duration_ms=2000,
        )
        self.assertTrue(request.audio_stream.readable())
        self.assertTrue(request.diarization)

    def test_request_rejects_path_like_or_non_audio_inputs(self) -> None:
        with self.assertRaises(ValueError):
            TranscriptionRequest(ASSET_ID, "private/path.wav", "audio/wav")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            TranscriptionRequest(ASSET_ID, BytesIO(b"x"), "application/octet-stream")

    def test_service_validates_adapter_asset_binding_and_duration(self) -> None:
        adapter = FakeSpeechToTextAdapter(valid_transcript())
        service = SpeechToTextService(adapter)
        request = TranscriptionRequest(
            ASSET_ID, BytesIO(b"audio"), "audio/wav", duration_ms=2000
        )
        self.assertEqual(service.transcribe(request), adapter.result)
        self.assertEqual(adapter.requests, [request])
        with self.assertRaises(TranscriptValidationError):
            service.transcribe(replace(request, duration_ms=1000))

    def test_rejects_segments_outside_duration_and_unordered_segments(self) -> None:
        transcript = valid_transcript()
        with self.assertRaises(TranscriptValidationError):
            validate_transcript(transcript, duration_ms=1000)
        later = replace(transcript.segments[0], start_ms=100, end_ms=300)
        earlier = replace(transcript.segments[0], segment_id="c2f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0102", start_ms=400, end_ms=600)
        with self.assertRaises(TranscriptValidationError):
            validate_transcript(replace(transcript, segments=(later, earlier)))

    def test_rejects_unknown_speakers_and_words_outside_segments(self) -> None:
        transcript = valid_transcript()
        bad_speaker = replace(transcript.segments[0], speaker_id="c2f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0102")
        with self.assertRaises(TranscriptValidationError):
            validate_transcript(replace(transcript, segments=(bad_speaker,)))
        bad_word = replace(transcript.segments[0].words[0], start_ms=0)
        bad_segment = replace(transcript.segments[0], words=(bad_word,))
        with self.assertRaises(TranscriptValidationError):
            validate_transcript(replace(transcript, segments=(bad_segment,)))

    def test_rejects_invalid_confidence_and_audio_provenance(self) -> None:
        transcript = valid_transcript()
        bad_segment = replace(transcript.segments[0], confidence=1.1)
        with self.assertRaises(TranscriptValidationError):
            validate_transcript(replace(transcript, segments=(bad_segment,)))
        bad_provenance = replace(transcript.provenance, audio_sha256="not-a-digest")
        with self.assertRaises(TranscriptValidationError):
            validate_transcript(replace(transcript, provenance=bad_provenance))

    def test_transcription_errors_expose_stable_retry_semantics(self) -> None:
        error = TranscriptionError(
            TranscriptionErrorCode.RATE_LIMITED,
            provider="fixture",
            retryable=True,
            retry_after_seconds=5,
        )
        self.assertEqual(error.code, "STT_RATE_LIMITED")
        self.assertTrue(error.retryable)
        self.assertEqual(error.retry_after_seconds, 5)
        self.assertNotIn("secret", str(error))


if __name__ == "__main__":
    unittest.main()
