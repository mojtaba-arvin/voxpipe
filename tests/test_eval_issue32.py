from __future__ import annotations

import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

from eval.issue32.metrics import compute_metrics
from eval.issue32.rules import label_conversation
from eval.issue32.templates import generate_conversations, write_dataset


ROOT = Path(__file__).resolve().parents[1]


class ExtractionSilverEvaluationTests(unittest.TestCase):
    def test_generator_is_reproducible_and_splits_documents(self) -> None:
        first = generate_conversations(count=100, seed=17)
        second = generate_conversations(count=100, seed=17)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 100)
        self.assertEqual(Counter(item["split"] for item in first), {"train": 70, "dev": 15, "test": 15})
        self.assertEqual(
            {item["scenario"] for item in first},
            {"order_confirmation", "address_change", "refund", "callback"},
        )
        schema = json.loads((ROOT / "eval" / "schemas" / "conversation.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        self.assertTrue(all(validator.is_valid(item) for item in first))
        self.assertTrue(all(item["source_kind"] == "synthetic_template" for item in first))

    def test_generator_rejects_counts_outside_100_to_200(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 100 and 200"):
            generate_conversations(count=99, seed=1)

    def test_dataset_metadata_records_seed_and_content_checksum(self) -> None:
        records = generate_conversations(count=100, seed=29)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "utterances.jsonl"
            metadata_path = Path(directory) / "metadata.json"
            metadata = write_dataset(records, output, metadata_path)
            self.assertEqual(metadata["seed"], 29)
            self.assertEqual(metadata["conversation_count"], 100)
            self.assertEqual(metadata["dataset_classification"], "synthetic_template_not_natural_conversation")
            self.assertEqual(len(metadata["sha256"]), 64)
            self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["sha256"], metadata["sha256"])

    def test_rules_extract_all_reference_slots_across_scenarios(self) -> None:
        records = generate_conversations(count=100, seed=31)
        for record in records:
            labels = label_conversation(record)
            for field in ("date", "amount", "order_id", "customer_name"):
                self.assertEqual(labels["fields"][field]["status"], "matched", (record["scenario"], field))
                self.assertEqual(
                    labels["fields"][field]["value"],
                    record["reference_slots"][field],
                    (record["scenario"], field),
                )
            self.assertEqual(labels["label_kind"], "deterministic_rule_silver")

    def test_multiple_distinct_values_are_marked_ambiguous(self) -> None:
        record = copy.deepcopy(generate_conversations(count=100, seed=37)[0])
        record["utterances"].append(
            {"utterance_id": "extra", "speaker_id": "customer", "text": "سفارش ORD-9999 نیز مطرح شد."}
        )
        labels = label_conversation(record)
        self.assertEqual(labels["fields"]["order_id"], {"value": None, "status": "ambiguous"})

    def test_metrics_report_micro_macro_and_disclose_synthetic_reference(self) -> None:
        records = generate_conversations(count=100, seed=41)
        predictions = [label_conversation(item) for item in records]
        result = compute_metrics(records, predictions)
        self.assertEqual(result["dataset_count"], 100)
        self.assertEqual(result["overall_micro"]["f1"], 1.0)
        self.assertEqual(result["overall_macro"]["f1"], 1.0)
        self.assertEqual(result["reference_kind"], "template_generated_reference_not_human_gold")
        self.assertEqual(result["split_counts"], {"dev": 15, "test": 15, "train": 70})
        schema = json.loads((ROOT / "eval" / "issue32" / "metrics.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(result)

    def test_wrong_prediction_counts_as_false_positive_and_false_negative(self) -> None:
        records = generate_conversations(count=100, seed=43)
        predictions = [label_conversation(item) for item in records]
        predictions[0]["fields"]["order_id"] = {"value": "ORD-9999", "status": "matched"}
        result = compute_metrics(records, predictions)
        self.assertEqual(result["fields"]["order_id"]["tp"], 99)
        self.assertEqual(result["fields"]["order_id"]["fp"], 1)
        self.assertEqual(result["fields"]["order_id"]["fn"], 1)

    def test_metrics_reject_missing_predictions_and_non_synthetic_sources(self) -> None:
        records = generate_conversations(count=100, seed=47)
        predictions = [label_conversation(item) for item in records]
        with self.assertRaisesRegex(ValueError, "prediction IDs do not match"):
            compute_metrics(records, predictions[:-1])
        non_synthetic = copy.deepcopy(records)
        non_synthetic[0]["source_kind"] = "read_speech"
        with self.assertRaisesRegex(ValueError, "synthetic_template"):
            compute_metrics(non_synthetic, predictions)


if __name__ == "__main__":
    unittest.main()
