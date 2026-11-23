import unittest

from voxpipe.speech.vad import (\n    SpeechRegion, VADRequest, VADResult, SileroVADAdapter, WebRTCVADAdapter, evaluate_vad,\n)


class SpeechRegionContractTests(unittest.TestCase):
    def test_accepts_valid_boundaries_and_optional_confidence(self):
        self.assertEqual(SpeechRegion(0, 10), SpeechRegion(0, 10, None))
        self.assertEqual(SpeechRegion(10, 20, 0.75).confidence, 0.75)

    def test_rejects_invalid_boundaries(self):
        invalid = [
            (-1, 10), (10, 10), (11, 10), (0.5, 10), (0, 10.5),
            (True, 10), (0, False),
        ]
        for start, end in invalid:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    SpeechRegion(start, end)

    def test_rejects_invalid_confidence(self):
        for confidence in (-0.01, 1.01, float("nan"), float("inf"), True, "0.5"):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    SpeechRegion(0, 10, confidence)


class VADResultAndAdapterValidationTests(unittest.TestCase):
    DIGEST = "a" * 64

    def test_vad_result_rejects_unsorted_overlapping_and_out_of_duration_regions(self):
        invalid = [
            (SpeechRegion(200, 300), SpeechRegion(100, 150)),
            (SpeechRegion(0, 200), SpeechRegion(150, 250)),
            (SpeechRegion(900, 1100),),
        ]
        for regions in invalid:
            with self.subTest(regions=regions):
                with self.assertRaises(ValueError):
                    VADResult("silero", "local", self.DIGEST, 16000, 1000, regions)

    def test_vad_result_accepts_sorted_non_overlapping_regions(self):
        result = VADResult(
            "silero", "local", self.DIGEST, 16000, 1000,
            (SpeechRegion(0, 100), SpeechRegion(200, 300)),
        )
        self.assertEqual(len(result.regions), 2)

    def test_silero_rejects_boolean_and_non_finite_configuration(self):
        for kwargs in (
            {"threshold": True},
            {"threshold": float("nan")},
            {"min_speech_duration_ms": True},
            {"min_silence_duration_ms": False},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    SileroVADAdapter(model=object(), timestamp_fn=lambda *a, **k: [], **kwargs)

    def test_silero_rejects_fractional_or_malformed_model_timestamps(self):
        adapter = SileroVADAdapter(
            model=object(), timestamp_fn=lambda *a, **k: [{"start": 0.5, "end": 100}]
        )
        request = VADRequest(tuple([0.0] * 16000), 16000, self.DIGEST)
        with self.assertRaisesRegex(ValueError, "integer sample boundaries"):
            adapter.detect_request(request)

    def test_webrtc_rejects_invalid_detector_and_digest(self):
        class FakeVAD:
            def is_speech(self, frame, sample_rate):
                return False

        with self.assertRaises(TypeError):
            WebRTCVADAdapter(vad=object())
        adapter = WebRTCVADAdapter(vad=FakeVAD())
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            adapter.detect(b"\\x00\\x00", sample_rate=16000, audio_sha256=None)

    def test_vad_evaluator_rejects_overlapping_annotation_regions(self):
        with self.assertRaisesRegex(ValueError, "ordered and non-overlapping"):
            evaluate_vad(
                (SpeechRegion(0, 200), SpeechRegion(150, 300)),
                (SpeechRegion(0, 200),), duration_ms=500,
            )


if __name__ == "__main__":
    unittest.main()
