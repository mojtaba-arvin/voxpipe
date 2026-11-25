"""Deterministic lexical-only retrieval baseline for silver pipeline validation.

This is intentionally not the production hybrid service and does not use embeddings.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from eval._shared.corpus_loader import load_corpus
from eval.issue48.qrels import jaccard
from eval.issue48.queries import _terms

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERIES = ROOT / "eval" / "artifacts" / "issue48" / "queries.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue48" / "run.json"
RUN_SCHEMA = ROOT / "eval" / "issue48" / "run.schema.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def rank_queries(
    queries: list[dict[str, Any]], corpus: list[dict[str, Any]], limit: int = 5
) -> dict[str, Any]:
    if not queries or not corpus:
        raise ValueError("queries and corpus must be non-empty")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    ranked = []
    for query in sorted(queries, key=lambda item: item["query_id"]):
        query_tokens = set(_terms(query["query_text"]))
        candidates = []
        for passage in corpus:
            score = jaccard(query_tokens, set(_terms(passage["text"])))
            candidates.append((score, passage["passage_id"]))
        # Stable tie-break: passage_id ascending after descending Jaccard.
        candidates.sort(key=lambda item: (-item[0], item[1]))
        ranked.append(
            {
                "query_id": query["query_id"],
                "split": query["split"],
                "ranked_passage_ids": [passage_id for _, passage_id in candidates[:limit]],
                "scores": [
                    {"passage_id": passage_id, "jaccard": round(score, 6)}
                    for score, passage_id in candidates[:limit]
                ],
            }
        )
    result = {
        "schema_version": 1,
        "ranking_method": "deterministic_jaccard_lexical_baseline",
        "query_count": len(queries),
        "corpus_count": len(corpus),
        "seed": queries[0]["seed"],
        "results": ranked,
    }
    schema = json.loads(RUN_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(f"run output failed schema validation: {errors[0].message}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--corpus-path", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    queries = read_jsonl(args.queries)
    corpus = load_corpus(args.corpus_path)
    result = rank_queries(queries, corpus, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("query_count", "corpus_count", "ranking_method")}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
