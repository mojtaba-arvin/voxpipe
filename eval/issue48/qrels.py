"""Build deterministic heuristic silver relevance judgments for retrieval queries."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from eval._shared.corpus_loader import load_corpus
from eval.issue48.queries import _terms

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERIES = ROOT / "eval" / "artifacts" / "issue48" / "queries.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue48" / "qrels.json"
QUERY_SCHEMA = ROOT / "eval" / "issue48" / "query.schema.json"
QRELS_SCHEMA = ROOT / "eval" / "issue48" / "qrels.schema.json"
LEXICAL_OVERLAP_THRESHOLD = 0.5
TIE_BREAKING = "passage_id_ascending"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"record on line {line_number} must be an object")
            records.append(record)
    return records


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def build_qrels(
    queries: list[dict[str, Any]], corpus: list[dict[str, Any]],
    threshold: float = LEXICAL_OVERLAP_THRESHOLD,
) -> dict[str, Any]:
    if not queries:
        raise ValueError("queries must contain at least one record")
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    query_schema = json.loads(QUERY_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(query_schema)
    query_validator = Draft202012Validator(query_schema)
    for query in queries:
        errors = list(query_validator.iter_errors(query))
        if errors:
            raise ValueError(f"query failed schema validation: {errors[0].message}")
    seeds = {query["seed"] for query in queries}
    if len(seeds) != 1:
        raise ValueError("all queries must use the same recorded seed")

    passages = {item["passage_id"]: item for item in corpus}
    if len(passages) != len(corpus):
        raise ValueError("corpus contains duplicate passage IDs")
    query_ids = [item["query_id"] for item in queries]
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("queries contain duplicate query IDs")

    qrels = []
    split_counts: Counter[str] = Counter()
    for query in sorted(queries, key=lambda item: item["query_id"]):
        source_id = query["source_passage_id"]
        if source_id not in passages:
            raise ValueError(f"{query['query_id']}: source passage {source_id!r} is missing")
        source_passage = passages[source_id]
        if query["source_kind"] != source_passage["source_kind"]:
            raise ValueError(f"{query['query_id']}: source kind does not match its passage")
        query_tokens = set(_terms(query["query_text"]))
        split_counts[query["split"]] += 1
        for passage_id in sorted(passages):
            passage = passages[passage_id]
            overlap = jaccard(query_tokens, set(_terms(passage["text"])))
            if passage_id == source_id:
                relevance = 2
            elif overlap >= threshold:
                relevance = 1
            else:
                relevance = 0
            qrels.append(
                {
                    "query_id": query["query_id"],
                    "passage_id": passage_id,
                    "relevance": relevance,
                    "jaccard": round(overlap, 6),
                    "split": query["split"],
                }
            )
    source_kinds = {item["source_kind"] for item in corpus}
    source_kind = next(iter(source_kinds)) if len(source_kinds) == 1 else "mixed_or_external"
    result = {
        "schema_version": 1,
        "source_kind": source_kind,
        "label_kind": "heuristic_lexical_silver",
        "seed": next(iter(seeds)),
        "threshold": threshold,
        "tie_breaking": TIE_BREAKING,
        "query_count": len(queries),
        "corpus_count": len(corpus),
        "split_counts": dict(sorted(split_counts.items())),
        "qrels": qrels,
    }
    schema = json.loads(QRELS_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(f"qrels output failed schema validation: {errors[0].message}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--corpus-path", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    queries = read_jsonl(args.queries)
    corpus = load_corpus(args.corpus_path)
    result = build_qrels(queries, corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("query_count", "corpus_count", "threshold", "split_counts", "label_kind")}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
