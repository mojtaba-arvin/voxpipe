import unittest

from voxpipe.speech.language_id import (
    LanguagePrediction, SpeechBrainLanguageIdentifier, calibrate_abstention_threshold,
)


class FakeClassifier:
    def __init__(self, scores, labels=("fa", "en")):
        self.scores = scores
        self.labels = labels

    def classify(self, samples, sample_rate):
        return self.scores, self.labels


class LanguageIdContractTests(unittest.TestCase):
    def test_prediction_rejects_invalid_confidence_and_timing(self):
        invalid = [
            {"confidence": float("nan")},
            {"confidence": 1.01},
            {"confidence": True},
            {"start_ms": -1},
            {"start_ms": True},
            {"start_ms": 10, "end_ms": 10},
            {"start_ms": 10, "end_ms": 9},
            {"provider": ""},
            {"model_version": ""},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                values = dict(language="fa", confidence=0.9)
                values.update(fields)
                with self.assertRaises(ValueError):
                    LanguagePrediction(**values)

    def test_identifier_rejects_boolean_and_out_of_range_samples(self):
        identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, 0.2]))
        for samples in ([0.0] * 15999 + [True], [0.0] * 15999 + [1.1]):
            with self.subTest(last_sample=samples[-1]):
                with self.assertRaises(ValueError):
                    identifier.identify(samples, sample_rate=16000)

    def test_identifier_rejects_invalid_non_winning_probability(self):
        identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, float("nan")]))
        with self.assertRaisesRegex(ValueError, "invalid probability"):
            identifier.identify([0.0] * 16000, sample_rate=16000)

    def test_abstention_calibrator_meets_accuracy_and_coverage_floors(self):
        references = ["fa", "fa", "de", "de"]
        predictions = [
            LanguagePrediction("fa", 0.99),
            LanguagePrediction("fa", 0.80),
            LanguagePrediction("en", 0.70),
            LanguagePrediction("de", 0.60),
        ]
        result = calibrate_abstention_threshold(
            references, predictions, min_selective_accuracy=0.95, min_coverage=0.50
        )
        self.assertTrue(result["feasible"])
        self.assertEqual(result["threshold"], 0.80)
        self.assertEqual(result["coverage"], 0.50)
        self.assertEqual(result["selective_accuracy"], 1.0)

    def test_abstention_calibrator_fails_closed_when_no_threshold_is_feasible(self):
        references = ["fa", "de"]
        predictions = [
            LanguagePrediction("fa", 0.5),
            LanguagePrediction("fa", 0.5),
        ]
        result = calibrate_abstention_threshold(
            references, predictions, min_selective_accuracy=0.95, min_coverage=0.50
        )
        self.assertFalse(result["feasible"])
        self.assertIsNone(result["threshold"])

    def test_identifier_rejects_boolean_probabilities_and_non_sequence_labels(self):
        for scores, labels in (([True, 0.2], ("fa", "en")), ([0.8, 0.2], "fa"), ([0.8, 0.2], (None, "en"))):
            identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier(scores, labels))
            with self.subTest(scores=scores, labels=labels):
                with self.assertRaisesRegex(ValueError, "malformed probability or label arrays"):
                    identifier.identify([0.0] * 16000, sample_rate=16000)

    def test_identifier_rejects_fractional_and_boolean_sample_rates(self):
        identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, 0.2]))
        for sample_rate in (16000.0, True):
            with self.subTest(sample_rate=sample_rate):
                with self.assertRaises(ValueError):
                    identifier.identify([0.0] * 16000, sample_rate=sample_rate)

    def test_identifier_rejects_boolean_threshold_and_invalid_model_version(self):
        for kwargs in ({"threshold": True}, {"threshold": float("nan")}, {"model_version": ""}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, 0.2]), **kwargs)

    def test_window_configuration_requires_integer_non_overrunning_hops(self):
        identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, 0.2]))
        for kwargs in (
            {"window_ms": 1000.5}, {"window_ms": True},
            {"hop_ms": 0.5}, {"hop_ms": True}, {"window_ms": 1000, "hop_ms": 1001},
            {"sample_rate": 16000.0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    identifier.identify_windows([0.0] * 32000, **kwargs)

    def test_identifier_rejects_empty_predicted_language_label(self):
        identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, 0.2], ("", "en")))
        with self.assertRaisesRegex(ValueError, "empty language label"):
            identifier.identify([0.0] * 16000, sample_rate=16000)


if __name__ == "__main__":
    unittest.main()
