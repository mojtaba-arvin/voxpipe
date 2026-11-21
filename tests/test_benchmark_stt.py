from __future__ import annotations

import unittest

from scripts.benchmark_stt import edit_distance, normalize, normalize_raw, score


class STTBenchmarkTests(unittest.TestCase):
    def test_persian_normalization_equivalences(self):
        self.assertEqual(normalize("مي‌كند، خانهٔ خوب"), normalize("می کند خانه خوب"))
        self.assertEqual(normalize("۱۲۳ ٤٥٦"), ["123", "456"])
        self.assertEqual(normalize("عَلِيّ ـ كِتاب"), normalize("علی کتاب"))

    def test_raw_score_is_reported_separately(self):
        result = score([{
            "reference": "مي‌روم",
            "hypothesis": "می روم",
            "audio_duration_seconds": 10,
            "inference_seconds": 5,
        }])
        self.assertEqual(result["normalization"], "fa-norm-v1")
        self.assertEqual(result["normalized"]["wer"], 0)
        self.assertGreater(result["raw"]["wer"], 0)
        self.assertEqual(result["wer"], result["normalized"]["wer"])

    def test_error_rates_rtf_and_cost(self):
        result = score([{
            "reference": "سلام دنیا",
            "hypothesis": "سلام جهان",
            "audio_duration_seconds": 10,
            "inference_seconds": 5,
            "cost_per_audio_minute": .01,
        }])
        self.assertEqual(result["wer"], .5)
        self.assertGreater(result["cer"], 0)
        self.assertEqual(result["real_time_factor"], .5)
        self.assertEqual(result["latency_seconds_p50"], 5)
        self.assertEqual(result["latency_seconds_p95"], 5)
        self.assertEqual(result["per_file_rtf_p50"], .5)
        self.assertAlmostEqual(result["estimated_api_cost_usd"], .01 / 6)

    def test_unknown_cost_is_not_reported_as_zero(self):
        result = score([{
            "reference": "hello", "hypothesis": "hello",
            "audio_duration_seconds": 60, "inference_seconds": 10,
        }])
        self.assertIsNone(result["estimated_api_cost_usd"])

    def test_edit_distance_empty(self):
        self.assertEqual(edit_distance([], ["x"]), 1)
        self.assertEqual(edit_distance(["x"], []), 1)

    def test_empty_reference_has_undefined_rates(self):
        result = score([{
            "reference": "", "hypothesis": "text",
            "audio_duration_seconds": 2, "inference_seconds": 1,
        }])
        self.assertIsNone(result["wer"])
        self.assertIsNone(result["cer"])

    def test_invalid_cost_and_timing_rejected(self):
        with self.assertRaises(ValueError):
            score([{
                "reference": "a", "hypothesis": "a",
                "audio_duration_seconds": 1, "inference_seconds": 1,
                "cost_per_audio_minute": -1,
            }])
        with self.assertRaises(ValueError):
            score([{
                "reference": "a", "hypothesis": "a",
                "audio_duration_seconds": 0, "inference_seconds": 1,
            }])

    def test_raw_normalizer_does_not_fold_arabic_letters(self):
        self.assertNotEqual(normalize_raw("ي"), normalize_raw("ی"))


if __name__ == "__main__":
    unittest.main()
