from __future__ import annotations
import unittest
from scripts.benchmark_stt import edit_distance, normalize, score


class STTBenchmarkTests(unittest.TestCase):
    def test_persian_normalization(self):
        self.assertEqual(normalize("مي كند، خانهٔ خوب"), normalize("می کند خانه خوب"))

    def test_error_rates_rtf_and_cost(self):
        result = score([{"reference":"سلام دنیا","hypothesis":"سلام جهان",
            "audio_duration_seconds":10,"inference_seconds":5,"cost_per_audio_minute":.01}])
        self.assertEqual(result["wer"], .5)
        self.assertGreater(result["cer"], 0)
        self.assertEqual(result["real_time_factor"], .5)
        self.assertAlmostEqual(result["estimated_api_cost_usd"], .01/6)

    def test_unknown_cost_is_not_reported_as_zero(self):
        result = score([{"reference":"hello","hypothesis":"hello",
            "audio_duration_seconds":60,"inference_seconds":10}])
        self.assertIsNone(result["estimated_api_cost_usd"])

    def test_edit_distance_empty(self):
        self.assertEqual(edit_distance([], ["x"]), 1)
        self.assertEqual(edit_distance(["x"], []), 1)


if __name__ == "__main__":
    unittest.main()
