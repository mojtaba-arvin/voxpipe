from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_persian_gold import DatasetError, evaluate, load_dataset, score_counts, validate_record_semantics


def record():
    transcript = "علی جلسه را جمعه برگزار می‌کند."
    quote = "جلسه را جمعه"
    start = transcript.index(quote)
    return {
        "schema_version": "1.0.0",
        "sample_id": "sample-001",
        "split": "test",
        "language": "fa-IR",
        "transcript_segments": {"segment-1": transcript},
        "annotations": {
            "entities": [{"value": {"name": "علی", "entity_type": "person"}, "evidence": [
                {"segment_id": "segment-1", "start_char": 0, "end_char": 3, "quote": "علی"}]}],
            "events": [], "topics": [], "relations": [], "action_items": [],
        },
        "review": {
            "status": "adjudicated", "annotator_ids": ["ann-1", "ann-2"],
            "adjudicator_id": "reviewer-3", "protocol_revision": "1.0",
        },
    }


class PersianGoldBenchmarkTests(unittest.TestCase):
    def test_exact_unicode_evidence_is_accepted(self):
        validate_record_semantics(record(), Path("test.jsonl"), 1)

    def test_wrong_evidence_offsets_are_rejected(self):
        item = record()
        item["annotations"]["entities"][0]["evidence"][0]["quote"] = "ع"
        with self.assertRaisesRegex(DatasetError, "does not exactly match"):
            validate_record_semantics(item, Path("test.jsonl"), 1)

    def test_adjudicator_must_be_independent(self):
        item = record()
        item["review"]["adjudicator_id"] = "ann-1"
        with self.assertRaisesRegex(DatasetError, "adjudicator must be independent"):
            validate_record_semantics(item, Path("test.jsonl"), 1)

    def test_duplicate_gold_claim_is_rejected(self):
        item = record()
        item["annotations"]["entities"].append(item["annotations"]["entities"][0])
        with self.assertRaisesRegex(DatasetError, "duplicate entities annotation"):
            validate_record_semantics(item, Path("test.jsonl"), 1)

    def test_manifest_counts_and_hashes_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            schema_path = ROOT / "benchmarks/persian-gold/record.schema.json"
            (directory / "record.schema.json").write_bytes(schema_path.read_bytes())
            item = record()
            raw = (json.dumps(item, ensure_ascii=False) + "\n").encode()
            (directory / "test.jsonl").write_bytes(raw)
            manifest = {
                "schema_version": "1.0.0", "status": "ready", "human_reviewed": True,
                "splits": {
                    "train": {"records": 0, "sha256": None},
                    "dev": {"records": 0, "sha256": None},
                    "test": {"records": 1, "sha256": hashlib.sha256(raw).hexdigest()},
                },
            }
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            loaded, splits = load_dataset(directory)
            self.assertEqual(len(splits["test"]), 1)
            manifest["splits"]["test"]["sha256"] = "0" * 64
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(DatasetError, "SHA-256"):
                load_dataset(directory)

    def test_exact_multiset_metrics_count_duplicate_predictions(self):
        gold = [{"sample_id": "s1", "annotations": {
            "entities": [{"value": {"name": "A"}}],
            "events": [], "topics": [], "relations": [], "action_items": [],
        }}]
        predicted = {"s1": {
            "entities": [{"value": {"name": "A"}}, {"value": {"name": "A"}}],
            "events": [], "topics": [], "relations": [], "action_items": [],
        }}
        report = evaluate(gold, predicted)
        self.assertEqual(report["per_type"]["entities"]["true_positive"], 1)
        self.assertEqual(report["per_type"]["entities"]["false_positive"], 1)
        self.assertEqual(report["per_type"]["entities"]["false_negative"], 0)

    def test_unsupported_category_metrics_are_undefined(self):
        metrics = score_counts(0, 0, 0)
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["recall"])
        self.assertIsNone(metrics["f1"])

    def test_macro_excludes_categories_without_gold_or_predictions(self):
        gold = [{"sample_id": "s1", "annotations": {
            "entities": [{"value": {"name": "A"}}],
            "events": [], "topics": [], "relations": [], "action_items": [],
        }}]
        predicted = {"s1": {
            "entities": [{"value": {"name": "A"}}],
            "events": [], "topics": [], "relations": [], "action_items": [],
        }}
        report = evaluate(gold, predicted)
        self.assertEqual(report["macro"]["f1"], 1.0)
        self.assertIsNone(report["per_type"]["events"]["f1"])

    def test_false_positive_only_category_scores_zero(self):
        self.assertEqual(score_counts(0, 1, 0)["precision"], 0.0)
        self.assertEqual(score_counts(0, 1, 0)["recall"], 0.0)
        self.assertEqual(score_counts(0, 1, 0)["f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
