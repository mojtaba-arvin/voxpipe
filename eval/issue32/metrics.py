"""Compute exact-match micro/macro extraction metrics against synthetic template slots."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "eval" / "artifacts" / "issue32" / "utterances.jsonl"
DEFAULT_LABELS = ROOT / "eval" / "artifacts" / "issue32" / "silver_labels.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue32" / "metrics_32.json"
SCHEMA_PATH = ROOT / "eval" / "issue32" / "metrics.schema.json"
CONVERSATION_SCHEMA_PATH = ROOT / "eval" / "schemas" / "conversation.schema.json"
LABELS_SCHEMA_PATH = ROOT / "eval" / "issue32" / "labels.schema.json"
FIELDS = ("date", "amount", "order_id", "customer_name")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
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


def _counts_to_metrics(counts: dict[str, int]) -> dict[str, Any]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def compute_metrics(
    conversations: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    if not conversations:
        raise ValueError("cannot score an empty dataset")
    if any(item.get("source_kind") != "synthetic_template" for item in conversations):
        raise ValueError("this evaluator accepts synthetic_template records only")

    conversation_schema = json.loads(CONVERSATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(conversation_schema)
    conversation_validator = Draft202012Validator(conversation_schema)
    for item in conversations:
        errors = list(conversation_validator.iter_errors(item))
        if errors:
            raise ValueError(f"invalid conversation input: {errors[0].message}")

    label_schema = json.loads(LABELS_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(label_schema)
    label_validator = Draft202012Validator(label_schema)
    for item in predictions:
        errors = list(label_validator.iter_errors(item))
        if errors:
            raise ValueError(f"invalid silver label input: {errors[0].message}")

    expected_seed = conversations[0].get("seed")
    if any(item.get("seed") != expected_seed for item in conversations):
        raise ValueError("all conversations must use the same recorded seed")

    references: dict[str, dict[str, Any]] = {}
    for item in conversations:
        identifier = item.get("conversation_id")
        if not isinstance(identifier, str) or identifier in references:
            raise ValueError(f"missing or duplicate conversation_id: {identifier!r}")
        references[identifier] = item

    predicted_by_id: dict[str, dict[str, Any]] = {}
    for item in predictions:
        identifier = item.get("conversation_id")
        if not isinstance(identifier, str) or identifier in predicted_by_id:
            raise ValueError(f"missing or duplicate prediction conversation_id: {identifier!r}")
        predicted_by_id[identifier] = item
    if set(references) != set(predicted_by_id):
        missing = sorted(set(references) - set(predicted_by_id))
        unexpected = sorted(set(predicted_by_id) - set(references))
        raise ValueError(f"prediction IDs do not match input; missing={missing[:5]}, unexpected={unexpected[:5]}")

    counts = {field: {"tp": 0, "fp": 0, "fn": 0} for field in FIELDS}
    split_counts: Counter[str] = Counter()
    for identifier, source in references.items():
        prediction = predicted_by_id[identifier]
        if prediction.get("label_kind") != "deterministic_rule_silver":
            raise ValueError(f"{identifier}: prediction is not a deterministic silver label")
        split_counts[source.get("split", "unknown")] += 1
        expected_fields = source["reference_slots"]
        predicted_fields = prediction["fields"]
        for field in FIELDS:
            expected = expected_fields[field]
            candidate = predicted_fields[field]
            actual = candidate["value"] if candidate["status"] == "matched" else None
            field_counts = counts[field]
            if expected is None:
                if actual is not None:
                    field_counts["fp"] += 1
            elif actual is None:
                field_counts["fn"] += 1
            elif actual == expected:
                field_counts["tp"] += 1
            else:
                # A wrong non-null value is both a false positive and a false negative.
                field_counts["fp"] += 1
                field_counts["fn"] += 1

    field_metrics = {field: _counts_to_metrics(counts[field]) for field in FIELDS}
    total_counts = {
        key: sum(field_counts[key] for field_counts in counts.values())
        for key in ("tp", "fp", "fn")
    }
    overall_micro = _counts_to_metrics(total_counts)
    overall_macro = {
        metric: round(sum(field_metrics[field][metric] for field in FIELDS) / len(FIELDS), 6)
        for metric in ("precision", "recall", "f1")
    }
    total = len(conversations)
    result = {
        "schema_version": 1,
        "source_kind": "synthetic_template",
        "label_kind": "deterministic_rule_silver",
        "reference_kind": "template_generated_reference_not_human_gold",
        "seed": expected_seed,
        "dataset_count": total,
        "split_counts": dict(sorted(split_counts.items())),
        "split_ratios": {
            split: round(count / total, 6) for split, count in sorted(split_counts.items())
        },
        "fields": field_metrics,
        "overall_micro": overall_micro,
        "overall_macro": overall_macro,
    }
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError(f"metrics output failed schema validation: {errors[0].message}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = compute_metrics(read_jsonl(args.input), read_jsonl(args.labels))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
