#!/usr/bin/env python3
"""Validate and score VoxPipe's adjudicated Persian structured-extraction gold set.

Gold annotations are never inferred or generated here. Evaluation is refused unless
the manifest declares a reviewed, frozen dataset and its split hashes match.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

COLLECTIONS = ("entities", "events", "topics", "relations", "action_items")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "benchmarks" / "persian-gold"


class DatasetError(ValueError):
    """The dataset or prediction file violates the benchmark contract."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{path}: cannot read valid UTF-8 JSON: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DatasetError(f"{path}: cannot read UTF-8 JSONL: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise DatasetError(f"{path}:{line_no}: blank lines are not allowed")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise DatasetError(f"{path}:{line_no}: each JSONL row must be an object")
        rows.append(row)
    return rows


def validate_record_semantics(record: dict[str, Any], source: Path, line_no: int) -> None:
    segments = record["transcript_segments"]
    review = record["review"]
    if review["adjudicator_id"] in review["annotator_ids"]:
        raise DatasetError(f"{source}:{line_no}: adjudicator must be independent of both annotators")
    seen: set[tuple[str, str]] = set()
    for collection in COLLECTIONS:
        for claim_index, claim in enumerate(record["annotations"][collection]):
            key = (collection, canonical(claim["value"]))
            if key in seen:
                raise DatasetError(
                    f"{source}:{line_no}: duplicate {collection} annotation at index {claim_index}"
                )
            seen.add(key)
            for evidence_index, evidence in enumerate(claim["evidence"]):
                segment_id = evidence["segment_id"]
                if segment_id not in segments:
                    raise DatasetError(
                        f"{source}:{line_no}: {collection}[{claim_index}] evidence[{evidence_index}] "
                        f"references unknown segment {segment_id!r}"
                    )
                text = segments[segment_id]
                start, end, quote = evidence["start_char"], evidence["end_char"], evidence["quote"]
                # Python string indices are Unicode code points, matching the protocol.
                if end <= start or end > len(text) or text[start:end] != quote:
                    raise DatasetError(
                        f"{source}:{line_no}: {collection}[{claim_index}] evidence[{evidence_index}] "
                        "does not exactly match its Unicode code-point offsets"
                    )


def load_dataset(dataset_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    manifest_path = dataset_dir / "manifest.json"
    schema_path = dataset_dir / "record.schema.json"
    manifest = read_json(manifest_path)
    schema = read_json(schema_path)
    if not isinstance(manifest, dict):
        raise DatasetError("manifest root must be a JSON object")
    if not isinstance(schema, dict):
        raise DatasetError("record schema root must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise DatasetError(f"record schema is invalid: {exc.message}") from exc
    validator = Draft202012Validator(schema)

    if manifest.get("schema_version") != "1.0.0":
        raise DatasetError("manifest schema_version must be 1.0.0")
    splits = manifest.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "dev", "test"}:
        raise DatasetError("manifest must declare exactly train, dev, and test splits")

    records_by_split: dict[str, list[dict[str, Any]]] = {}
    sample_ids: set[str] = set()
    for split in ("train", "dev", "test"):
        entry = splits[split]
        if not isinstance(entry, dict) or not isinstance(entry.get("records"), int) or isinstance(entry.get("records"), bool) or entry["records"] < 0:
            raise DatasetError(f"manifest splits.{split}.records must be an integer")
        path = dataset_dir / f"{split}.jsonl"
        if path.exists():
            rows = read_jsonl(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if rows else None
        else:
            rows, digest = [], None
        if len(rows) != entry["records"]:
            raise DatasetError(
                f"{split}: manifest says {entry['records']} records, found {len(rows)}"
            )
        if entry.get("sha256") != digest:
            raise DatasetError(f"{split}: manifest SHA-256 does not match {path.name}")
        for line_no, record in enumerate(rows, 1):
            errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))
            if errors:
                error = errors[0]
                location = "/".join(str(part) for part in error.absolute_path) or "<root>"
                raise DatasetError(f"{path}:{line_no}: schema violation at {location}: {error.message}")
            if record["split"] != split:
                raise DatasetError(f"{path}:{line_no}: record split does not match its file")
            if record["sample_id"] in sample_ids:
                raise DatasetError(f"{path}:{line_no}: duplicate sample_id {record['sample_id']!r}")
            sample_ids.add(record["sample_id"])
            validate_record_semantics(record, path, line_no)
        records_by_split[split] = rows

    total = sum(len(rows) for rows in records_by_split.values())
    reviewed = manifest.get("human_reviewed") is True
    status = manifest.get("status")
    if reviewed and (status != "ready" or total == 0 or not records_by_split["test"]):
        raise DatasetError("human_reviewed=true requires status=ready and a non-empty test split")
    if status == "ready" and not reviewed:
        raise DatasetError("status=ready requires human_reviewed=true")
    return manifest, records_by_split


def score_counts(tp: int, fp: int, fn: int) -> dict[str, int | float | None]:
    # A category absent from both gold and predictions has no measurable quality.
    # Keep its metrics null instead of awarding a misleading perfect score.
    if tp + fp + fn == 0:
        precision = recall = f1 = None
    else:
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def load_predictions(path: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for line_no, row in enumerate(read_jsonl(path), 1):
        sample_id = row.get("sample_id")
        annotations = row.get("annotations")
        if not isinstance(sample_id, str) or not sample_id:
            raise DatasetError(f"{path}:{line_no}: sample_id must be a non-empty string")
        if sample_id in result:
            raise DatasetError(f"{path}:{line_no}: duplicate prediction sample_id {sample_id!r}")
        if not isinstance(annotations, dict) or set(annotations) != set(COLLECTIONS):
            raise DatasetError(f"{path}:{line_no}: annotations must contain exactly {COLLECTIONS}")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for collection in COLLECTIONS:
            claims = annotations[collection]
            if not isinstance(claims, list):
                raise DatasetError(f"{path}:{line_no}: {collection} must be an array")
            for index, claim in enumerate(claims):
                if not isinstance(claim, dict) or not isinstance(claim.get("value"), dict):
                    raise DatasetError(f"{path}:{line_no}: {collection}[{index}] requires an object value")
            normalized[collection] = claims
        result[sample_id] = normalized
    return result


def evaluate(gold_rows: list[dict[str, Any]], predictions: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    gold_by_id = {row["sample_id"]: row["annotations"] for row in gold_rows}
    missing = sorted(set(gold_by_id) - set(predictions))
    extra = sorted(set(predictions) - set(gold_by_id))
    if missing or extra:
        raise DatasetError(
            f"prediction coverage must exactly match test split (missing={missing[:10]}, extra={extra[:10]})"
        )

    per_type: dict[str, Any] = {}
    totals = [0, 0, 0]
    for collection in COLLECTIONS:
        tp = fp = fn = 0
        for sample_id, gold in gold_by_id.items():
            predicted_values = Counter(canonical(claim["value"]) for claim in predictions[sample_id][collection])
            gold_values = Counter(canonical(claim["value"]) for claim in gold[collection])
            matched = predicted_values & gold_values
            tp += sum(matched.values())
            fp += sum(predicted_values.values()) - sum(matched.values())
            fn += sum(gold_values.values()) - sum(matched.values())
        per_type[collection] = score_counts(tp, fp, fn)
        totals = [a + b for a, b in zip(totals, (tp, fp, fn))]

    macro = {
        metric: (
            sum(per_type[name][metric] for name in COLLECTIONS if per_type[name][metric] is not None)
            / sum(per_type[name][metric] is not None for name in COLLECTIONS)
            if any(per_type[name][metric] is not None for name in COLLECTIONS) else None
        )
        for metric in ("precision", "recall", "f1")
    }
    return {
        "matching": "exact-json-value-multiset-v1",
        "documents": len(gold_rows),
        "claims": {name: sum(len(row["annotations"][name]) for row in gold_rows) for name in COLLECTIONS},
        "per_type": per_type,
        "micro": score_counts(*totals),
        "macro": macro,
        "warning": "Exact-value scores do not measure semantic equivalence; review error examples and retain the frozen test set.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "evaluate"))
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path, help="JSONL predictions for every test sample (evaluate only)")
    args = parser.parse_args()
    try:
        manifest, records = load_dataset(args.dataset_dir)
        total = sum(map(len, records.values()))
        if args.command == "validate":
            print(json.dumps({
                "valid": True, "dataset": manifest.get("dataset"),
                "status": manifest.get("status"), "human_reviewed": manifest.get("human_reviewed"),
                "records": {split: len(rows) for split, rows in records.items()},
                "evaluation_ready": manifest.get("human_reviewed") is True and bool(records["test"]),
            }, ensure_ascii=False, indent=2))
            return 0
        if args.predictions is None:
            parser.error("evaluate requires --predictions")
        if manifest.get("human_reviewed") is not True or manifest.get("status") != "ready":
            raise DatasetError("refusing to score: manifest does not declare a human-reviewed ready dataset")
        if not records["test"]:
            raise DatasetError("refusing to score: frozen test split is empty")
        report = evaluate(records["test"], load_predictions(args.predictions))
        report["dataset"] = manifest.get("dataset")
        report["dataset_version"] = manifest.get("version")
        report["test_sha256"] = manifest["splits"]["test"]["sha256"]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
