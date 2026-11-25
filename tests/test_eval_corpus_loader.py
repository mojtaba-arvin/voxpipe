from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from eval._shared.corpus_loader import load_corpus


ROOT = Path(__file__).resolve().parents[1]


class EvaluationCorpusLoaderTests(unittest.TestCase):
    def test_uses_five_record_synthetic_placeholder_by_default(self) -> None:
        with patch.dict(os.environ, {"CORPUS_PATH": ""}):
            passages = load_corpus()
        self.assertEqual(len(passages), 5)
        self.assertTrue(all(item["source_kind"] == "synthetic_template" for item in passages))
        self.assertEqual(len({item["passage_id"] for item in passages}), 5)

    def test_reads_and_validates_external_corpus_path(self) -> None:
        record = {
            "passage_id": "external-1",
            "text": "متن نمونهٔ بیرونی برای ارزیابی بازیابی.",
            "language": "fa-IR",
            "source_kind": "read_speech",
            "split": "test",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.jsonl"
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"CORPUS_PATH": str(path)}):
                self.assertEqual(load_corpus(), [record])

    def test_rejects_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.jsonl"
            path.write_text('{"passage_id":"x","text":""}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid corpus record on line 1"):
                load_corpus(path)

    def test_rejects_duplicate_passage_ids(self) -> None:
        record = {
            "passage_id": "duplicate",
            "text": "متن",
            "language": "fa-IR",
            "source_kind": "synthetic_template",
            "split": "train",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.jsonl"
            line = json.dumps(record, ensure_ascii=False)
            path.write_text(f"{line}\n{line}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate passage_id"):
                load_corpus(path)

    def test_conversation_and_retrieval_corpus_schemas_are_distinct(self) -> None:
        conversation_schema = json.loads(
            (ROOT / "eval" / "schemas" / "conversation.schema.json").read_text(encoding="utf-8")
        )
        corpus_schema = json.loads(
            (ROOT / "eval" / "schemas" / "corpus.schema.json").read_text(encoding="utf-8")
        )
        conversation = {
            "conversation_id": "conversation-1",
            "language": "fa-IR",
            "source_kind": "synthetic_template",
            "split": "train",
            "seed": 1,
            "scenario": "order_confirmation",
            "reference_slots": {
                "customer_name": "مشتری نمونه 001",
                "phone": "PHONE-PLACEHOLDER-001",
                "order_id": "ORD-1001",
                "amount": "120000",
                "date": "1405/01/01",
            },
            "utterances": [{"utterance_id": "u1", "speaker_id": "s1", "text": "سلام"}],
        }
        passage = {
            "passage_id": "passage-1",
            "text": "سلام",
            "language": "fa-IR",
            "source_kind": "synthetic_template",
            "split": "train",
        }
        self.assertTrue(Draft202012Validator(conversation_schema).is_valid(conversation))
        self.assertFalse(Draft202012Validator(corpus_schema).is_valid(conversation))
        self.assertTrue(Draft202012Validator(corpus_schema).is_valid(passage))
        self.assertFalse(Draft202012Validator(conversation_schema).is_valid(passage))


if __name__ == "__main__":
    unittest.main()
