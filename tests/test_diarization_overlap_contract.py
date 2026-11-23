import unittest

from voxpipe.speech.diarization import (
    DiarizationRequest, DiarizationResult, ECAPADiarizationAdapter, SpeakerTurn,
)
from voxpipe.speech.vad import SpeechRegion


DIGEST = "a" * 64


class DiarizationOverlapContractTests(unittest.TestCase):
    def test_rejects_overlapping_turns_when_overlap_is_not_supported(self):
        turns = (
            SpeakerTurn(0, 1500, "speaker_1", 0.9),
            SpeakerTurn(1000, 2000, "speaker_2", 0.8),
        )
        with self.assertRaisesRegex(ValueError, "overlapping speaker turns"):
            DiarizationResult("test", "test-model", DIGEST, turns)

    def test_allows_overlapping_turns_only_when_capability_is_explicit(self):
        turns = (
            SpeakerTurn(0, 1500, "speaker_1", 0.9),
            SpeakerTurn(1000, 2000, "speaker_2", 0.8),
        )
        result = DiarizationResult(
            "test", "test-model", DIGEST, turns, overlap_supported=True
        )
        self.assertTrue(result.overlap_supported)
        self.assertEqual(result.turns, turns)

    def test_keeps_adjacent_turns_valid_for_non_overlap_baseline(self):
        turns = (
            SpeakerTurn(0, 1000, "speaker_1", 0.9),
            SpeakerTurn(1000, 2000, "speaker_2", 0.8),
        )
        result = DiarizationResult("test", "test-model", DIGEST, turns)
        self.assertFalse(result.overlap_supported)


class DiarizationRequestAndAdapterTests(unittest.TestCase):
    def test_request_rejects_unordered_or_overlapping_speech_regions(self):
        samples = [0.0] * 16000
        invalid_regions = (
            (SpeechRegion(200, 400), SpeechRegion(100, 150)),
            (SpeechRegion(0, 500), SpeechRegion(400, 700)),
        )
        for regions in invalid_regions:
            with self.subTest(regions=regions):
                with self.assertRaises(ValueError):
                    DiarizationRequest(samples, DIGEST, regions)

    def test_request_rejects_non_16khz_and_non_normalized_samples(self):
        with self.assertRaises(ValueError):
            DiarizationRequest([0.0] * 16000, DIGEST, (), sample_rate=8000)
        invalid = [0.0] * 15999 + [1.1]
        with self.assertRaises(ValueError):
            DiarizationRequest(invalid, DIGEST, ())

    def test_adapter_rejects_injected_embedder_without_embed_method(self):
        with self.assertRaises(TypeError):
            ECAPADiarizationAdapter(embedder=object(), similarity_threshold=0.7)

    def test_adapter_runs_with_valid_local_embedder_contract(self):
        class FakeEmbedder:
            def embed(self, samples, sample_rate):
                return [1.0, 0.0]

        adapter = ECAPADiarizationAdapter(embedder=FakeEmbedder(), similarity_threshold=0.7)
        request = DiarizationRequest(
            [0.0] * 16000, DIGEST, (SpeechRegion(0, 1000),)
        )
        result = adapter.diarize_request(request)
        self.assertEqual(len(result.turns), 1)
        self.assertEqual(result.turns[0].speaker_id, "speaker_1")
        self.assertFalse(result.overlap_supported)

if __name__ == "__main__":
    unittest.main()
