import unittest

from voxpipe.speech.diarization import (
    DiarizationRequest, ECAPADiarizationAdapter, SpeakerTurn, evaluate_diarization,
)
from voxpipe.speech.vad import SpeechRegion

DIGEST = "a" * 64


class FakeEmbedder:
    def embed(self, samples, sample_rate):
        return [1.0, 0.0]


class DiarizationAdapterInputValidationTests(unittest.TestCase):
    def test_request_rejects_fractional_or_boolean_sample_rate(self):
        for sample_rate in (16000.0, True):
            with self.subTest(sample_rate=sample_rate):
                with self.assertRaises(ValueError):
                    DiarizationRequest((0.0,) * 16000, DIGEST, (), sample_rate=sample_rate)

    def test_direct_adapter_reuses_request_validation_for_samples_and_regions(self):
        adapter = ECAPADiarizationAdapter(
            embedder=lambda samples, sample_rate: [1.0, 0.0],
            window_ms=1000, similarity_threshold=0.8,
        )
        invalid = (
            ([True] * 32000, 16000, [SpeechRegion(0, 2000)]),
            ([1.1] * 32000, 16000, [SpeechRegion(0, 2000)]),
            ([0.0] * 32000, 16000.0, [SpeechRegion(0, 2000)]),
            ([0.0] * 32000, 16000, [SpeechRegion(0, 2100)]),
            ([0.0] * 32000, 16000, [SpeechRegion(0, 1500), SpeechRegion(1000, 2000)]),
        )
        for samples, sample_rate, regions in invalid:
            with self.subTest(sample_rate=sample_rate, regions=regions):
                with self.assertRaises(ValueError):
                    adapter.diarize(samples, sample_rate=sample_rate,
                        audio_sha256=DIGEST, speech_regions=regions)

    def test_adapter_rejects_invalid_window_and_similarity_configuration(self):
        for kwargs in (
            {"window_ms": 1000.0, "similarity_threshold": 0.8},
            {"window_ms": True, "similarity_threshold": 0.8},
            {"window_ms": 1000, "similarity_threshold": float("nan")},
            {"window_ms": 1000, "similarity_threshold": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    ECAPADiarizationAdapter(embedder=FakeEmbedder(), **kwargs)

    def test_adapter_rejects_inconsistent_embedding_dimensions(self):
        class ChangingEmbedder:
            def __init__(self):
                self.calls = 0

            def embed(self, samples, sample_rate):
                self.calls += 1
                return [1.0, 0.0] if self.calls == 1 else [1.0, 0.0, 0.0]

        adapter = ECAPADiarizationAdapter(
            embedder=ChangingEmbedder(), window_ms=1000, similarity_threshold=0.8
        )
        audio = [0.5] * 64000
        with self.assertRaisesRegex(ValueError, "inconsistent vector dimensions"):
            adapter.diarize(audio, sample_rate=16000, audio_sha256=DIGEST,
                speech_regions=[SpeechRegion(0, 4000)])

    def test_evaluator_rejects_invalid_duration_and_out_of_bounds_turns(self):
        with self.assertRaisesRegex(ValueError, "evaluation options"):
            evaluate_diarization((), (), duration_ms=100.0)
        with self.assertRaisesRegex(ValueError, "within duration_ms"):
            evaluate_diarization((SpeakerTurn(0, 101, "speaker_1", 0.9),), (),
                duration_ms=100, collar_ms=0)


if __name__ == "__main__":
    unittest.main()
