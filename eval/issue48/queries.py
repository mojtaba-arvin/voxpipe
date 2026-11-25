"""Generate reproducible Persian retrieval queries from a validated corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from eval._shared.corpus_loader import load_corpus

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue48" / "queries.jsonl"
DEFAULT_METADATA = ROOT / "eval" / "artifacts" / "issue48" / "metadata_48.json"
TOKEN_RE = re.compile(r"[A-Za-z0-9-]+|[\u0600-\u06FF]{2,}")
STOPWORDS = {
    "این", "برای", "در", "و", "به", "را", "از", "است", "شود", "می‌شود",
    "با", "من", "مشتری", "فرضی", "سفارش", "متن", "نمونه", "درباره",
}
TEMPLATES = (
    "در متن، «{term}» چه جزئیاتی دارد؟",
    "کدام بخش دربارهٔ «{term}» توضیح می‌دهد؟",
    "اطلاعات مربوط به «{term}» در کدام متن آمده است؟",
    "در این متن، «{term}» چگونه توصیف شده است؟",
)


def _terms(text: str) -> list[str]:
    terms = []
    for token in TOKEN_RE.findall(text):
        normalized = token.strip(".,!?؟؛:()[]{}«»\"'").casefold()
        if len(normalized) >= 3 and normalized not in STOPWORDS and normalized not in terms:
            terms.append(normalized)
    return terms


def generate_queries(
    corpus: list[dict[str, Any]], count: int = 120, seed: int = 20261125
) -> list[dict[str, Any]]:
    if not 100 <= count <= 200:
        raise ValueError("count must be between 100 and 200")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if not corpus:
        raise ValueError("corpus must contain at least one passage")

    passages = sorted(corpus, key=lambda item: item["passage_id"])
    terms_by_id = {item["passage_id"]: _terms(item["text"]) for item in passages}
    if any(not terms for terms in terms_by_id.values()):
        raise ValueError("every source passage must contain at least one searchable term")

    rng = random.Random(seed)
    per_passage: Counter[str] = Counter()
    records = []
    for index in range(count):
        passage = passages[index % len(passages)]
        passage_id = passage["passage_id"]
        local_index = per_passage[passage_id]
        per_passage[passage_id] += 1
        terms = terms_by_id[passage_id]
        term = terms[rng.randrange(len(terms))]
        template = TEMPLATES[local_index % len(TEMPLATES)]
        records.append(
            {
                "query_id": f"query-{passage_id}-{local_index + 1:03d}",
                "query_text": template.format(term=term),
                "source_passage_id": passage_id,
                "anchor_terms": [term],
                "language": passage["language"],
                "source_kind": passage["source_kind"],
                "query_kind": "deterministic_template_silver",
                "split": "train",
                "seed": seed,
            }
        )

    rng.shuffle(records)
    train_end = int(count * 0.70)
    dev_end = train_end + int(count * 0.15)
    for position, record in enumerate(records):
        record["split"] = "train" if position < train_end else "dev" if position < dev_end else "test"
    return records


def write_queries(
    records: list[dict[str, Any]], corpus: list[dict[str, Any]],
    output_path: Path = DEFAULT_OUTPUT, metadata_path: Path = DEFAULT_METADATA,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records)
    output_path.write_text(payload, encoding="utf-8")
    corpus_payload = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in sorted(corpus, key=lambda x: x["passage_id"]))
    metadata = {
        "schema_version": 1,
        "source_kind": "synthetic_template" if all(x["source_kind"] == "synthetic_template" for x in corpus) else "mixed_or_external",
        "query_kind": "deterministic_template_silver",
        "seed": records[0]["seed"] if records else None,
        "query_count": len(records),
        "corpus_count": len(corpus),
        "split_counts": dict(sorted(Counter(item["split"] for item in records).items())),
        "corpus_sha256": hashlib.sha256(corpus_payload.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20261125)
    parser.add_argument("--corpus-path", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    args = parser.parse_args()
    corpus = load_corpus(args.corpus_path)
    records = generate_queries(corpus, args.count, args.seed)
    metadata = write_queries(records, corpus, args.output, args.metadata)
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
