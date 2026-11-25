"""Load and validate a retrieval evaluation corpus.

The default corpus is explicitly synthetic. Set CORPUS_PATH to evaluate an
independently prepared JSONL corpus; this loader never downloads data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPOSITORY_ROOT / "eval" / "schemas" / "corpus.schema.json"
_PLACEHOLDER_PATH = _REPOSITORY_ROOT / "eval" / "_placeholder" / "corpus.jsonl"
_MAX_CORPUS_BYTES = 20 * 1024 * 1024
_MAX_PASSAGES = 100_000


def _validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_corpus(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return validated passages from *path* or the synthetic placeholder.

    An explicit path takes precedence over CORPUS_PATH. Blank CORPUS_PATH is
    treated as unset. Invalid JSONL, schema violations, duplicate IDs, empty
    corpora, and oversized files fail closed with a line-aware error.
    """
    selected = path if path is not None else os.environ.get("CORPUS_PATH") or _PLACEHOLDER_PATH
    corpus_path = Path(selected).expanduser()
    try:
        corpus_path = corpus_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"corpus path does not exist: {corpus_path}") from exc
    if not corpus_path.is_file():
        raise ValueError(f"corpus path is not a regular file: {corpus_path}")
    if corpus_path.stat().st_size > _MAX_CORPUS_BYTES:
        raise ValueError(f"corpus exceeds {_MAX_CORPUS_BYTES} bytes")

    validator = _validator()
    passages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with corpus_path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                passage = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on corpus line {line_number}: {exc.msg}") from exc
            errors = sorted(validator.iter_errors(passage), key=lambda error: tuple(str(part) for part in error.path))
            if errors:
                error = errors[0]
                location = ".".join(str(part) for part in error.path) or "<root>"
                raise ValueError(
                    f"invalid corpus record on line {line_number} at {location}: {error.message}"
                )
            passage_id = passage["passage_id"]
            if passage_id in seen_ids:
                raise ValueError(f"duplicate passage_id {passage_id!r} on line {line_number}")
            seen_ids.add(passage_id)
            passages.append(passage)
            if len(passages) > _MAX_PASSAGES:
                raise ValueError(f"corpus exceeds {_MAX_PASSAGES} passages")

    if not passages:
        raise ValueError("corpus contains no passages")
    return passages
