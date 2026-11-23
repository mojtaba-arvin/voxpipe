import unittest

from voxpipe.speech.language_id import LanguagePrediction, SpeechBrainLanguageIdentifier


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

    def test_identifier_rejects_empty_predicted_language_label(self):
        identifier = SpeechBrainLanguageIdentifier(model=FakeClassifier([0.8, 0.2], ("", "en")))
        with self.assertRaisesRegex(ValueError, "empty language label"):
            identifier.identify([0.0] * 16000, sample_rate=16000)


if __name__ == "__main__":
    unittest.main()
