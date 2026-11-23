import unittest

from voxpipe.speech.diarization import (
    DiarizationRequest, ECAPADiarizationAdapter, SpeakerTurn,
)
from voxpipe.speech.vad import SpeechRegion

DIGEST = "a" * 64


class DiarizationSharedSpeechIntelligenceTests(unittest.TestCase):
    def test_common_diarization_request_and_anonymous_speaker_turn_contract(self):
        class Embedder:
            def embed(self, samples, sample_rate):
                return [1.0, 0.0] if samples[0] > 0 else [0.0, 1.0]

        samples = tuple([0.5] * 32000 + [-0.5] * 32000)
        request = DiarizationRequest(samples, DIGEST, (SpeechRegion(0, 4000),))
        adapter = ECAPADiarizationAdapter(
            embedder=Embedder(), window_ms=1000, similarity_threshold=.8
        )
        result = adapter.diarize_request(request)
        self.assertEqual([turn.speaker_id for turn in result.turns], ["speaker_1", "speaker_2"])
        self.assertFalse(result.overlap_supported)

    def test_diarization_request_rejects_bad_audio_and_out_of_bounds_regions(self):
        with self.assertRaises(ValueError):
            DiarizationRequest((0.0, float("nan")), DIGEST, ())
        with self.assertRaises(ValueError):
            DiarizationRequest((0.0,), DIGEST, (), sample_rate=44100)
        with self.assertRaises(ValueError):
            DiarizationRequest((0.0,) * 16000, DIGEST, (SpeechRegion(0, 2000),))
        with self.assertRaises(ValueError):
            SpeakerTurn(-1, 100, "speaker_1", .9)
        with self.assertRaises(ValueError):
            SpeakerTurn(0, 100, "speaker_1", float("nan"))


if __name__ == "__main__":
    unittest.main()
