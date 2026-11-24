"""Deepgram adapter tests use a fake HTTP transport; no key, network or spend."""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from voxpipe.speech.contracts import TranscriptionError, TranscriptionRequest
from voxpipe.speech.deepgram import DeepgramNova3Adapter

ASSET = "9d6eaa44-70c4-4d42-91d8-47d4d4c6f002"
DIGEST = "a" * 64


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload[:size]


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {
            "results": {
                "channels": [{"alternatives": [{
                    "transcript": "سلام hello",
                    "languages": ["fa", "en"],
                    "words": [
                        {"word": "سلام", "punctuated_word": "سلام", "start": 0.1, "end": 0.5,
                         "confidence": 0.99, "speaker": 0},
                        {"word": "hello", "punctuated_word": "hello", "start": 0.6, "end": 1.0,
                         "confidence": 0.95, "speaker": 1},
                    ],
                }]}],
                "utterances": [
                    {"start": 0.1, "end": 0.5, "transcript": "سلام", "confidence": 0.99,
                     "speaker": 0, "words": [{"word": "سلام", "punctuated_word": "سلام",
                     "start": 0.1, "end": 0.5, "confidence": 0.99, "speaker": 0}]},
                    {"start": 0.6, "end": 1.0, "transcript": "hello", "confidence": 0.95,
                     "speaker": 1, "words": [{"word": "hello", "punctuated_word": "hello",
                     "start": 0.6, "end": 1.0, "confidence": 0.95, "speaker": 1}]},
                ],
            }
        }
        self.error = error
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return FakeResponse(json.dumps(self.payload).encode())


def make_request(**kwargs):
    return TranscriptionRequest(
        asset_id=ASSET, audio_stream=BytesIO(b"synthetic-audio"),
        content_type="audio/wav", audio_sha256=DIGEST, duration_ms=2000, **kwargs,
    )


class DeepgramAdapterTests(unittest.TestCase):
    def test_multilingual_words_speakers_and_provenance_are_normalized(self):
        transport = FakeTransport()
        adapter = DeepgramNova3Adapter(api_key="test-key", http_opener=transport)
        result = adapter.transcribe(make_request(language_hint="fa-IR", diarization=True))
        self.assertEqual(result.language, "fa")
        self.assertEqual(result.text, "سلام hello")
        self.assertEqual(result.provenance.provider, "deepgram")
        self.assertEqual(result.provenance.model, "nova-3")
        self.assertEqual(result.provenance.audio_sha256, DIGEST)
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(len(result.speakers), 2)
        self.assertEqual(result.segments[0].words[0].text, "سلام")
        self.assertEqual(result.segments[1].speaker_id, result.speakers[1].speaker_id)
        request, timeout = transport.requests[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query["model"], ["nova-3"])
        self.assertEqual(query["language"], ["multi"])
        self.assertEqual(query["language_hint"], ["fa"])
        self.assertEqual(query["diarize_model"], ["latest"])
        self.assertEqual(timeout, 60.0)
        self.assertEqual(request.get_header("Authorization"), "Token test-key")
        self.assertEqual(request.data, b"synthetic-audio")

    def test_missing_key_fails_before_reading_audio_or_network(self):
        stream = BytesIO(b"audio")
        request = TranscriptionRequest(ASSET, stream, "audio/wav", DIGEST)
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(TranscriptionError) as caught:
                DeepgramNova3Adapter(api_key=None).transcribe(request)
        self.assertEqual(caught.exception.code, "STT_PROVIDER_UNAVAILABLE")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(stream.tell(), 0)

    def test_word_timestamps_can_be_omitted_without_losing_utterance_times(self):
        result = DeepgramNova3Adapter(
            api_key="test-key", http_opener=FakeTransport()
        ).transcribe(make_request(word_timestamps=False))
        self.assertEqual(result.segments[0].words, ())
        self.assertEqual(result.segments[0].start_ms, 100)

    def test_rate_limit_maps_retry_after_without_exposing_provider_body(self):
        error = HTTPError(
            "https://api.deepgram.com/v1/listen", 429, "Too Many Requests",
            {"Retry-After": "7"}, None,
        )
        adapter = DeepgramNova3Adapter(api_key="test-key", http_opener=FakeTransport(error=error))
        with self.assertRaises(TranscriptionError) as caught:
            adapter.transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_RATE_LIMITED")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.retry_after_seconds, 7)
        self.assertNotIn("Too Many Requests", str(caught.exception))

    def test_authentication_failure_is_not_retryable(self):
        error = HTTPError("https://api.deepgram.com/v1/listen", 401, "Unauthorized", {}, None)
        adapter = DeepgramNova3Adapter(api_key="bad-key", http_opener=FakeTransport(error=error))
        with self.assertRaises(TranscriptionError) as caught:
            adapter.transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_AUTHENTICATION_FAILED")
        self.assertFalse(caught.exception.retryable)

    def test_ambiguous_network_failure_is_not_automatically_retryable(self):
        adapter = DeepgramNova3Adapter(api_key="test-key", http_opener=FakeTransport(error=TimeoutError()))
        with self.assertRaises(TranscriptionError) as caught:
            adapter.transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_PROVIDER_UNAVAILABLE")
        self.assertFalse(caught.exception.retryable)

    def test_invalid_provider_response_is_redacted(self):
        adapter = DeepgramNova3Adapter(
            api_key="test-key", http_opener=FakeTransport(payload={"private": "payload"})
        )
        with self.assertRaises(TranscriptionError) as caught:
            adapter.transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_RESPONSE_INVALID")
        self.assertNotIn("private", str(caught.exception))

    def test_response_size_is_bounded(self):
        transport = FakeTransport()
        adapter = DeepgramNova3Adapter(
            api_key="test-key", max_response_bytes=4, http_opener=transport
        )
        with self.assertRaises(TranscriptionError) as caught:
            adapter.transcribe(make_request())
        self.assertEqual(caught.exception.code, "STT_RESPONSE_INVALID")

    def test_diarization_fails_closed_if_speaker_labels_are_missing(self):
        payload = {
            "results": {
                "channels": [{"alternatives": [{"transcript": "سلام", "languages": ["fa"]}]}],
                "utterances": [{"start": 0.1, "end": 0.5, "transcript": "سلام", "words": []}],
            }
        }
        adapter = DeepgramNova3Adapter(
            api_key="test-key", http_opener=FakeTransport(payload=payload)
        )
        with self.assertRaises(TranscriptionError) as caught:
            adapter.transcribe(make_request(diarization=True))
        self.assertEqual(caught.exception.code, "STT_RESPONSE_INVALID")


if __name__ == "__main__":
    unittest.main()
