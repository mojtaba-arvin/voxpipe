import unittest
from types import SimpleNamespace

from voxpipe.speech.language_id import (
    calibrate_abstention_threshold,
    evaluate_language_id,
)


class LanguageIDCalibrationContractTests(unittest.TestCase):
    def test_selective_metrics_include_coverage_and_abstention(self):
        predictions = [
            SimpleNamespace(language="fa", confidence=.9),
            SimpleNamespace(language="und", confidence=.4),
        ]
        metrics = evaluate_language_id(["fa", "en"], predictions)
        self.assertEqual(metrics["coverage"], .5)
        self.assertEqual(metrics["selective_accuracy"], 1.0)
        self.assertEqual(metrics["abstention_rate"], .5)

    def test_calibration_maximizes_coverage_subject_to_accuracy_floor(self):
        predictions = [
            SimpleNamespace(language="fa", confidence=.95),
            SimpleNamespace(language="fa", confidence=.80),
            SimpleNamespace(language="en", confidence=.55),
            SimpleNamespace(language="fa", confidence=.40),
        ]
        result = calibrate_abstention_threshold(
            ["fa", "fa", "en", "en"], predictions,
            min_selective_accuracy=1.0, min_coverage=.5,
        )
        self.assertTrue(result["feasible"])
        self.assertEqual(result["threshold"], .55)
        self.assertEqual(result["coverage"], .75)
        self.assertEqual(result["selective_accuracy"], 1.0)

    def test_calibration_fails_closed_when_constraints_conflict(self):
        predictions = [
            SimpleNamespace(language="fa", confidence=.9),
            SimpleNamespace(language="en", confidence=.8),
        ]
        result = calibrate_abstention_threshold(
            ["fa", "fa"], predictions,
            min_selective_accuracy=1.0, min_coverage=1.0,
        )
        self.assertFalse(result["feasible"])
        self.assertIsNone(result["threshold"])

    def test_calibration_rejects_pre_abstained_predictions(self):
        with self.assertRaises(ValueError):
            calibrate_abstention_threshold(
                ["fa"], [SimpleNamespace(language="und", confidence=.9)]
            )


if __name__ == "__main__":
    unittest.main()
