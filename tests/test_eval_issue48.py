from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

from eval._shared.corpus_loader import load_corpus
from eval.issue48.lexical_baseline import rank_queries
from eval.issue48.metrics import compute_metrics
from eval.issue48.qrels import build_qrels
from eval.issue48.queries import generate_queries


ROOT = Path(__file__).resolve().parents[1]


class RetrievalSilverEvaluationTests(unittest.TestCase):
    def test_query_generator_is_reproducible_and_uses_placeholder_corpus(self) -> None:
        corpus = load_corpus()
        first = generate_queries(corpus, count=100, seed=7)
        second = generate_queries(corpus, count=100, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 100)
        self.assertEqual(Counter(item["split"] for item in first), {"train": 70, "dev": 15, "test": 15})
        passage_ids = {item["passage_id"] for item in corpus}
        self.assertTrue(all(item["source_passage_id"] in passage_ids for item in first))
        schema = json.loads((ROOT / "eval" / "issue48" / "query.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        self.assertTrue(all(validator.is_valid(item) for item in first))

    def test_query_generator_rejects_out_of_range_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 100 and 200"):
            generate_queries(load_corpus(), count=99, seed=1)

    def test_qrels_always_mark_source_relevant_and_apply_documented_threshold(self) -> None:
        corpus = [
            {"passage_id": "p1", "text": "alpha beta gamma", "language": "en", "source_kind": "synthetic_template", "split": "test"},
            {"passage_id": "p2", "text": "alpha beta delta", "language": "en", "source_kind": "synthetic_template", "split": "test"},
        ]
        queries = [{
            "query_id": "q1",
            "query_text": "alpha beta",
            "source_passage_id": "p1",
            "anchor_terms": ["alpha", "beta"],
            "language": "en",
            "source_kind": "synthetic_template",
            "query_kind": "deterministic_template_silver",
            "split": "test",
            "seed": 11,
        }]
        result = build_qrels(queries, corpus)
        pairs = {item["passage_id"]: item for item in result["qrels"]}
        self.assertEqual(pairs["p1"]["relevance"], 2)
        self.assertEqual(pairs["p2"]["relevance"], 1)
        self.assertEqual(result["threshold"], 0.5)
        self.assertEqual(result["tie_breaking"], "passage_id_ascending")
        schema = json.loads((ROOT / "eval" / "issue48" / "qrels.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(result)

    def test_lexical_baseline_is_deterministic_and_tie_breaks_by_passage_id(self) -> None:
        corpus = [
            {"passage_id": "p2", "text": "alpha beta", "language": "en", "source_kind": "synthetic_template", "split": "test"},
            {"passage_id": "p1", "text": "gamma delta", "language": "en", "source_kind": "synthetic_template", "split": "test"},
        ]
        query = [{
            "query_id": "q1", "query_text": "not-present-token", "source_passage_id": "p1",
            "anchor_terms": ["not-present-token"], "language": "en", "source_kind": "synthetic_template",
            "query_kind": "deterministic_template_silver", "split": "test", "seed": 13,
        }]
        first = rank_queries(query, corpus, limit=2)
        self.assertEqual(first, rank_queries(query, corpus, limit=2))
        self.assertEqual(first["results"][0]["ranked_passage_ids"], ["p1", "p2"])

    def test_metrics_use_existing_evaluator_and_disclose_small_placeholder_limit(self) -> None:
        corpus = load_corpus()
        queries = generate_queries(corpus, count=100, seed=17)
        qrels = build_qrels(queries, corpus)
        run = rank_queries(queries, corpus, limit=5)
        result = compute_metrics(queries, qrels, run)
        self.assertEqual(result["query_count"], 100)
        self.assertEqual(result["test_query_count"], 15)
        self.assertEqual(result["recall_at_5"], 1.0)
        self.assertTrue(any("saturated" in warning for warning in result["warnings"]))
        self.assertIn("not VoxPipe hybrid", " ".join(result["warnings"]))
        schema = json.loads((ROOT / "eval" / "issue48" / "metrics.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(result)


    def test_cli_pipeline_runs_queries_qrels_ranker_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            queries = temp / "queries.jsonl"
            metadata = temp / "metadata.json"
            qrels = temp / "qrels.json"
            run = temp / "run.json"
            metrics = temp / "metrics.json"
            env = os.environ.copy()
            env["CORPUS_PATH"] = ""
            commands = [
                [
                    sys.executable, "-m", "eval.issue48.queries",
                    "--count", "100", "--seed", "23",
                    "--output", str(queries), "--metadata", str(metadata),
                ],
                [
                    sys.executable, "-m", "eval.issue48.qrels",
                    "--queries", str(queries), "--output", str(qrels),
                ],
                [
                    sys.executable, "-m", "eval.issue48.lexical_baseline",
                    "--queries", str(queries), "--output", str(run),
                ],
                [
                    sys.executable, "-m", "eval.issue48.metrics",
                    "--queries", str(queries), "--qrels", str(qrels),
                    "--run", str(run), "--output", str(metrics),
                ],
            ]
            for command in commands:
                result = subprocess.run(
                    command, cwd=ROOT, env=env, check=False, capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertEqual(report["query_count"], 100)
            self.assertEqual(report["test_query_count"], 15)
            self.assertEqual(report["recall_at_5"], 1.0)
            self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["seed"], 23)

    def test_qrels_reject_missing_source_passage(self) -> None:
        queries = [{
            "query_id": "q1", "query_text": "alpha", "source_passage_id": "missing",
            "anchor_terms": ["alpha"], "language": "en", "source_kind": "synthetic_template",
            "query_kind": "deterministic_template_silver", "split": "test", "seed": 1,
        }]
        corpus = [{"passage_id": "p1", "text": "alpha beta", "language": "en", "source_kind": "synthetic_template", "split": "test"}]
        with self.assertRaisesRegex(ValueError, "source passage"):
            build_qrels(queries, corpus)


if __name__ == "__main__":
    unittest.main()
