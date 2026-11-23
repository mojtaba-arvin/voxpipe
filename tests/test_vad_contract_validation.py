import unittest

from voxpipe.speech.vad import SpeechRegion


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


if __name__ == "__main__":
    unittest.main()
