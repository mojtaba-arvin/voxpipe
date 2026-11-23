from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from compare_persian_annotations import AnnotationError, compare, read_rows


def row(*, sample_id="sample-1", text="علی گزارش فروش را ارسال کرد.", entities=None, topics=None):
    entities = entities or []
    topics = topics or []
    return {
        "sample_id": sample_id,
        "transcript_segments": {"segment-1": text},
        "annotations": {
            "entities": entities,
            "events": [],
            "topics": topics,
            "relations": [],
            "action_items": [],
        },
    }


def claim(value, quote, text="علی گزارش فروش را ارسال کرد."):
    start = text.index(quote)
    return {
        "value": value,
        "evidence": [{
            "segment_id": "segment-1",
            "start_char": start,
            "end_char": start + len(quote),
            "quote": quote,
        }],
    }


class AnnotationAgreementTests(unittest.TestCase):
    def test_reports_per_category_exact_claim_and_evidence_agreement(self):
        text = "علی گزارش فروش را ارسال کرد."
        left = {
            "sample-1": row(
                text=text,
                entities=[claim({"name": "علی", "entity_type": "person"}, "علی", text)],
                topics=[claim({"name": "گزارش فروش"}, "گزارش فروش", text)],
            )
        }
        right = {
            "sample-1": row(
                text=text,
                entities=[claim({"name": "علی", "entity_type": "person"}, "علی", text)],
            )
        }
        report = compare(left, right)
        self.assertEqual(report["per_type"]["entities"]["f1"], 1.0)
        self.assertEqual(report["per_type"]["topics"]["annotator_a_only"], 1)
        self.assertEqual(report["micro"]["matched"], 1)
        self.assertEqual(report["evidence"]["exact_evidence_match_rate"], 1.0)
        self.assertIn("not Cohen's kappa", report["interpretation"])

    def test_same_value_with_different_valid_evidence_is_not_exact_evidence_agreement(self):
        text = "علی و مریم گزارش فروش را بررسی کردند."
        left = {
            "sample-1": row(
                text=text,
                entities=[claim({"name": "گزارش فروش", "entity_type": "document"}, "گزارش فروش", text)],
            )
        }
        right = {
            "sample-1": row(
                text=text,
                entities=[claim({"name": "گزارش فروش", "entity_type": "document"}, "مریم", text)],
            )
        }
        report = compare(left, right)
        self.assertEqual(report["micro"]["matched"], 1)
        self.assertEqual(report["evidence"]["exact_evidence_matches"], 0)
        self.assertEqual(report["evidence"]["exact_evidence_match_rate"], 0.0)

    def test_rejects_different_sample_coverage(self):
        with self.assertRaisesRegex(AnnotationError, "sample coverage differs"):
            compare({"a": row(sample_id="a")}, {"b": row(sample_id="b")})

    def test_rejects_different_source_transcript_for_same_sample(self):
        left = {"sample-1": row(text="علی آمد.")}
        right = {"sample-1": row(text="مریم آمد.")}
        with self.assertRaisesRegex(AnnotationError, "transcript source differs"):
            compare(left, right)

    def test_rejects_invalid_unicode_evidence_offsets(self):
        bad = row(entities=[{
            "value": {"name": "علی", "entity_type": "person"},
            "evidence": [{
                "segment_id": "segment-1", "start_char": 1, "end_char": 4, "quote": "علی"
            }],
        }])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "annotations.jsonl"
            path.write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(AnnotationError, "exact Unicode offsets"):
                read_rows(path)

    def test_rejects_claim_values_outside_category_contract(self):
        bad = row(entities=[claim({"name": "علی"}, "علی")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "annotations.jsonl"
            path.write_text(json.dumps(bad, ensure_ascii=False) + "\\n", encoding="utf-8")
            with self.assertRaisesRegex(AnnotationError, "value fields"):
                read_rows(path)

    def test_rejects_unhashable_or_unknown_evidence_segment_ids(self):
        bad = row(entities=[{
            "value": {"name": "علی", "entity_type": "person"},
            "evidence": [{
                "segment_id": ["segment-1"], "start_char": 0, "end_char": 3, "quote": "علی"
            }],
        }])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "annotations.jsonl"
            path.write_text(json.dumps(bad, ensure_ascii=False) + "\\n", encoding="utf-8")
            with self.assertRaisesRegex(AnnotationError, "unknown segment_id"):
                read_rows(path)

    def test_rejects_duplicate_sample_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "annotations.jsonl"
            line = json.dumps(row(), ensure_ascii=False)
            path.write_text(line + "\n" + line + "\n", encoding="utf-8")
            with self.assertRaisesRegex(AnnotationError, "duplicate sample_id"):
                read_rows(path)


if __name__ == "__main__":
    unittest.main()
