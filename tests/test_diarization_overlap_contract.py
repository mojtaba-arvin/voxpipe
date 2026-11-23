import unittest

from voxpipe.speech.diarization import DiarizationResult, SpeakerTurn


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


if __name__ == "__main__":
    unittest.main()
