"""Compute Recall@5, MRR and binary nDCG@5 for the silver retrieval baseline."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from voxpipe.retrieval.evaluation import evaluate_retrieval  # noqa: E402

DEFAULT_QUERIES = ROOT / "eval" / "artifacts" / "issue48" / "queries.jsonl"
DEFAULT_QRELS = ROOT / "eval" / "artifacts" / "issue48" / "qrels.json"
DEFAULT_RUN = ROOT / "eval" / "artifacts" / "issue48" / "run.json"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue48" / "metrics_48.json"
QUERY_SCHEMA = ROOT / "eval" / "issue48" / "query.schema.json"
QRELS_SCHEMA = ROOT / "eval" / "issue48" / "qrels.schema.json"
RUN_SCHEMA = ROOT / "eval" / "issue48" / "run.schema.json"
METRICS_SCHEMA = ROOT / "eval" / "issue48" / "metrics.schema.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"record on line {line_number} must be an object")
            records.append(value)
    return records


def _load_validated(path: Path, schema_path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError(f"{path.name} failed schema validation: {errors[0].message}")
    return value


def compute_metrics(
    queries: list[dict[str, Any]], qrels: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    if not queries:
        raise ValueError("queries must contain at least one record")
    query_schema = json.loads(QUERY_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(query_schema)
    query_validator = Draft202012Validator(query_schema)
    for query in queries:
        errors = list(query_validator.iter_errors(query))
        if errors:
            raise ValueError(f"query failed schema validation: {errors[0].message}")

    for value, schema_path, label in (
        (qrels, QRELS_SCHEMA, "qrels"),
        (run, RUN_SCHEMA, "run"),
    ):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(value))
        if errors:
            raise ValueError(f"{label} input failed schema validation: {errors[0].message}")
    if len({query["seed"] for query in queries}) != 1:
        raise ValueError("all queries must use the same seed")
    if qrels["seed"] != run["seed"] or qrels["seed"] != queries[0]["seed"]:
        raise ValueError("query, qrels and run seeds must match")
    query_by_id = {item["query_id"]: item for item in queries}
    if len(query_by_id) != len(queries):
        raise ValueError("queries contain duplicate IDs")
    run_by_id = {item["query_id"]: item for item in run["results"]}
    if len(run_by_id) != len(run["results"]):
        raise ValueError("run contains duplicate query IDs")
    if set(run_by_id) != set(query_by_id):
        raise ValueError("run query IDs must exactly match generated query IDs")
    qrels_query_ids = {item["query_id"] for item in qrels["qrels"]}
    if qrels_query_ids != set(query_by_id):
        raise ValueError("qrels query IDs must exactly match generated query IDs")
    if qrels["query_count"] != len(queries) or run["query_count"] != len(queries):
        raise ValueError("query counts must match across queries, qrels and run")
    if qrels["corpus_count"] != run["corpus_count"]:
        raise ValueError("corpus counts must match across qrels and run")

    test_ids = {identifier for identifier, query in query_by_id.items() if query["split"] == "test"}
    if not test_ids:
        raise ValueError("test split must contain at least one query")
    relevance: dict[str, set[str]] = {}
    for query_id in sorted(test_ids):
        relevant = {
            item["passage_id"]
            for item in qrels["qrels"]
            if item["query_id"] == query_id and item["relevance"] >= 1
        }
        if not relevant:
            raise ValueError(f"{query_id}: qrels contain no relevant passage")
        relevance[query_id] = relevant

    ranked_results = {
        query_id: run_by_id[query_id]["ranked_passage_ids"]
        for query_id in test_ids if query_id in run_by_id
    }
    calculated = evaluate_retrieval(relevance, ranked_results, ks=(5,))
    split_counts = dict(sorted(Counter(query["split"] for query in queries).items()))
    warnings = [
        "Heuristic qrels are silver labels, not human relevance judgments.",
        "The Jaccard ranker is lexical-only and is not VoxPipe hybrid/semantic retrieval.",
    ]
    if qrels["source_kind"] == "synthetic_template":
        warnings.append("Synthetic template results are pipeline evidence only, not Persian production-quality evidence.")
    elif qrels["source_kind"] == "read_speech":
        warnings.append("Read-speech results do not establish natural-conversation performance.")
    elif qrels["source_kind"] == "consented_natural":
        warnings.append("Consent does not turn heuristic qrels into human judgments; keep participant and privacy controls in force.")
    elif qrels["source_kind"] == "mixed_or_external":
        warnings.append("Mixed/external corpus claims require separate provenance and privacy review.")
    if qrels["corpus_count"] <= 5:
        warnings.append("Recall@5 is saturated for a corpus of five or fewer passages; use a larger corpus for meaningful Recall@5 discrimination.")

    result = {
        "schema_version": 1,
        "source_kind": qrels["source_kind"],
        "qrels_kind": qrels["label_kind"],
        "ranking_method": run["ranking_method"],
        "seed": qrels["seed"],
        "query_count": len(queries),
        "test_query_count": calculated["query_count"],
        "corpus_count": qrels["corpus_count"],
        "split_counts": split_counts,
        "split_ratios": {
            split: round(count / len(queries), 6) for split, count in split_counts.items()
        },
        "jaccard_threshold": qrels["threshold"],
        "relevance_policy": "qrels grades >= 1 are binary relevant for Recall@5, MRR and nDCG@5",
        "recall_at_5": calculated["recall_at_k"]["5"],
        "mrr": calculated["mrr"],
        "ndcg_at_5": calculated["ndcg"],
        "warnings": warnings,
    }
    schema = json.loads(METRICS_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(f"metrics output failed schema validation: {errors[0].message}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--qrels", type=Path, default=DEFAULT_QRELS)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    qrels = _load_validated(args.qrels, QRELS_SCHEMA)
    run = _load_validated(args.run, RUN_SCHEMA)
    result = compute_metrics(read_jsonl(args.queries), qrels, run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
