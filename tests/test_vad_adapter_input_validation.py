import unittest

from voxpipe.speech.vad import (
    SileroVADAdapter, SpeechRegion, VADResult, WebRTCVADAdapter, evaluate_vad,
)

DIGEST = "a" * 64


class VADAdapterInputValidationTests(unittest.TestCase):
    def setUp(self):
        self.silero = SileroVADAdapter(
            model=object(), timestamp_fn=lambda *args, **kwargs: []
        )

    def test_silero_direct_api_reuses_normalized_vad_request_validation(self):
        invalid_inputs = (
            ([True] * 16000, 16000, DIGEST),
            ([1.01] * 16000, 16000, DIGEST),
            ([float("nan")] * 16000, 16000, DIGEST),
            ([0.0] * 16000, 16000.0, DIGEST),
            ([0.0] * 16000, 16000, "invalid"),
        )
        for samples, sample_rate, digest in invalid_inputs:
            with self.subTest(sample_rate=sample_rate, digest=digest):
                with self.assertRaises(ValueError):
                    self.silero.detect(samples, sample_rate=sample_rate, audio_sha256=digest)

    def test_vad_result_rejects_overlapping_or_out_of_duration_regions(self):
        with self.assertRaisesRegex(ValueError, "ordered, non-overlapping"):
            VADResult("silero", "local", DIGEST, 16000, 100,
                (SpeechRegion(0, 60), SpeechRegion(50, 80)))
        with self.assertRaisesRegex(ValueError, "within audio duration"):
            VADResult("silero", "local", DIGEST, 16000, 100,
                (SpeechRegion(0, 101),))

    def test_vad_result_rejects_non_integer_duration_and_invalid_provenance(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            VADResult("silero", "local", DIGEST, 16000, True, ())
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            VADResult("silero", "local", "bad", 16000, 100, ())
        with self.assertRaisesRegex(ValueError, "bounded string"):
            VADResult("", "local", DIGEST, 16000, 100, ())

    def test_webrtc_rejects_fractional_or_boolean_configuration(self):
        for kwargs in (
            {"aggressiveness": True},
            {"frame_duration_ms": 10.0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    WebRTCVADAdapter(vad=object(), **kwargs)
        adapter = WebRTCVADAdapter(vad=object())
        with self.assertRaisesRegex(ValueError, "sample rate"):
            adapter.detect(b"\x00\x00" * 160, sample_rate=16000.0, audio_sha256=DIGEST)

    def test_vad_evaluator_rejects_invalid_durations_and_regions(self):
        with self.assertRaisesRegex(ValueError, "positive integers"):
            evaluate_vad((), (), duration_ms=100.0)
        with self.assertRaisesRegex(ValueError, "within duration_ms"):
            evaluate_vad((SpeechRegion(0, 101),), (), duration_ms=100)


if __name__ == "__main__":
    unittest.main()
