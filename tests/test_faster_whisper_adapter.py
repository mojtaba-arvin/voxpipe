"""Offline tests for faster-whisper adapter; no model, network or API key."""
from __future__ import annotations

import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voxpipe.speech import SpeechToTextService, TranscriptionError, TranscriptionRequest
from voxpipe.speech.faster_whisper import FasterWhisperAdapter

ASSET = "9d6eaa44-70c4-4d42-91d8-47d4d4c6f002"
DIGEST = "a" * 64


class FakeModel:
    def __init__(self, segments=None, language="fa"):
        self.segments = segments if segments is not None else [SimpleNamespace(
            start=.12, end=1.82, text=" سلام، جلسه را شروع می‌کنیم. ",
            words=[
                SimpleNamespace(start=.12, end=.51, word="سلام،"),
                SimpleNamespace(start=.54, end=.89, word="جلسه"),
                SimpleNamespace(start=.90, end=1.04, word="را"),
                SimpleNamespace(start=1.06, end=1.82, word="شروع می‌کنیم."),
            ],
        )]
        self.language = language
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        self.audio_path = Path(audio)
        self.exists_during_call = self.audio_path.is_file()
        return iter(self.segments), SimpleNamespace(language=self.language)


def make_request(**kwargs):
    return TranscriptionRequest(
        asset_id=ASSET, audio_stream=BytesIO(b"synthetic-audio-fixture"),
        content_type="audio/wav", audio_sha256=DIGEST, **kwargs,
    )


class FasterWhisperTests(unittest.TestCase):
    def test_persian_transcript_timestamps_provenance_and_cleanup(self):
        model = FakeModel()
        result = SpeechToTextService(FasterWhisperAdapter(model=model)).transcribe(
            make_request(language_hint="fa-IR", duration_ms=2000)
        )
        self.assertEqual(result.language, "fa")
        self.assertEqual(result.text, "سلام، جلسه را شروع می‌کنیم.")
        self.assertEqual(result.provenance.provider, "faster-whisper")
        self.assertEqual(result.provenance.audio_sha256, DIGEST)
        self.assertEqual(result.segments[0].start_ms, 120)
        self.assertEqual(result.segments[0].words[0].start_ms, 120)
        self.assertEqual(len(result.segments[0].words), 4)
        self.assertTrue(model.exists_during_call)
        self.assertFalse(model.audio_path.exists())
        self.assertEqual(model.calls[0]["language"], "fa")
        self.assertTrue(model.calls[0]["vad_filter"])

    def test_word_timestamps_can_be_disabled(self):
        model = FakeModel()
        result = FasterWhisperAdapter(model=model).transcribe(make_request(word_timestamps=False))
        self.assertEqual(result.segments[0].words, ())
        self.assertFalse(model.calls[0]["word_timestamps"])

    def test_diarization_is_explicitly_unsupported(self):
        with self.assertRaises(TranscriptionError) as caught:
            FasterWhisperAdapter(model=FakeModel()).transcribe(make_request(diarization=True))
        self.assertEqual(caught.exception.code, "STT_FEATURE_UNSUPPORTED")

    def test_oversized_audio_is_rejected_and_tempfile_removed(self):
        adapter = FasterWhisperAdapter(model=FakeModel(), max_audio_bytes=3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            with patch("voxpipe.speech.faster_whisper.tempfile.mkstemp") as mocked:
                def create_temp(**kwargs):
                    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
                    return fd, str(path)
                mocked.side_effect = create_temp
                with self.assertRaises(TranscriptionError) as caught:
                    adapter.transcribe(make_request())
            self.assertEqual(caught.exception.code, "STT_INVALID_AUDIO")
            self.assertFalse(path.exists())

    def test_empty_model_output_is_valid(self):
        result = FasterWhisperAdapter(model=FakeModel(segments=[])).transcribe(make_request())
        self.assertEqual(result.text, "")
        self.assertEqual(result.segments, ())

    def test_provider_exception_does_not_leak_internal_text(self):
        class Broken:
            def transcribe(self, *args, **kwargs):
                raise RuntimeError("private path and secret")
        with self.assertRaises(TranscriptionError) as caught:
            FasterWhisperAdapter(model=Broken()).transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_INVALID_AUDIO")
        self.assertNotIn("secret", str(caught.exception))

    def test_missing_local_runtime_maps_to_stable_error(self):
        adapter = FasterWhisperAdapter(model_name="not-cached")
        with patch("voxpipe.speech.faster_whisper.importlib.import_module", side_effect=ImportError):
            with self.assertRaises(TranscriptionError) as caught:
                adapter.transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_PROVIDER_UNAVAILABLE")
        self.assertFalse(caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
