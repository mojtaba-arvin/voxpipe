from types import SimpleNamespace
import unittest

from scripts.run_ollama_structured_smoke import build_smoke_acceptance


class StructuredSmokeGateTests(unittest.TestCase):
    @staticmethod
    def metrics(*, topic_f1, relation_f1, micro_f1):
        return {
            "per_type": {
                "events": SimpleNamespace(f1=1.0),
                "topics": SimpleNamespace(f1=topic_f1),
                "relations": SimpleNamespace(f1=relation_f1),
            },
            "micro": SimpleNamespace(f1=micro_f1),
        }

    def test_v14_category_metrics_pass_all_synthetic_smoke_gates(self):
        result = build_smoke_acceptance(
            self.metrics(topic_f1=0.6667, relation_f1=1.0, micro_f1=0.9167),
            successes=4, total_samples=4, non_empty_samples=4, injection_pass=True,
        )
        self.assertTrue(result["passed"])

    def test_topic_regression_fails_smoke_even_when_schema_and_events_pass(self):
        result = build_smoke_acceptance(
            self.metrics(topic_f1=0.0, relation_f1=1.0, micro_f1=0.7692),
            successes=4, total_samples=4, non_empty_samples=4, injection_pass=True,
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["topic_f1_at_least_0_5"])
        self.assertFalse(result["micro_f1_at_least_0_9"])

    def test_relation_regression_fails_smoke(self):
        result = build_smoke_acceptance(
            self.metrics(topic_f1=0.6667, relation_f1=0.5, micro_f1=0.9167),
            successes=4, total_samples=4, non_empty_samples=4, injection_pass=True,
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["relation_f1_is_one"])


if __name__ == "__main__":
    unittest.main()
