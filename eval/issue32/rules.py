"""Deterministic rule-based silver labels for synthetic Persian conversations."""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "eval" / "artifacts" / "issue32" / "utterances.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue32" / "silver_labels.jsonl"
CONVERSATION_SCHEMA = ROOT / "eval" / "schemas" / "conversation.schema.json"
LABELS_SCHEMA = ROOT / "eval" / "issue32" / "labels.schema.json"
DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

DATE_RE = re.compile(r"(?<!\d)(14\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?!\d)")
AMOUNT_RE = re.compile(r"(?:مبلغ(?:\s+سفارش)?|بازپرداخت)\s*([\d,٬]+)\s*تومان")
ORDER_RE = re.compile(r"\bORD-\d{4}\b")
NAME_RE = re.compile(r"مشتری نمونه\s+\d{3}")


@lru_cache(maxsize=2)
def _validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def normalize_digits(text: str) -> str:
    return text.translate(DIGIT_TRANSLATION)


def _extract_one(
    pattern: re.Pattern[str], text: str, normalize: Any = None, group: int = 0
) -> dict[str, Any]:
    matches = [match.group(group) for match in pattern.finditer(text)]
    if normalize is not None:
        matches = [normalize(value) for value in matches]
    unique = list(dict.fromkeys(matches))
    if not unique:
        return {"value": None, "status": "null"}
    if len(unique) > 1:
        return {"value": None, "status": "ambiguous"}
    return {"value": unique[0], "status": "matched"}


def _normalize_amount(value: str) -> str:
    return re.sub(r"[,٬]", "", value)


def label_conversation(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("source_kind") != "synthetic_template":
        raise ValueError("rule-based silver labels are restricted to synthetic_template records")
    errors = list(_validator(CONVERSATION_SCHEMA).iter_errors(record))
    if errors:
        raise ValueError(f"invalid conversation record: {errors[0].message}")

    text = normalize_digits("\n".join(item["text"] for item in record["utterances"]))
    fields = {
        "date": _extract_one(DATE_RE, text),
        "amount": _extract_one(AMOUNT_RE, text, _normalize_amount, group=1),
        "order_id": _extract_one(ORDER_RE, text),
        "customer_name": _extract_one(NAME_RE, text),
    }
    result = {
        "conversation_id": record["conversation_id"],
        "source_kind": record["source_kind"],
        "label_kind": "deterministic_rule_silver",
        "fields": fields,
    }
    errors = list(_validator(LABELS_SCHEMA).iter_errors(result))
    if errors:
        raise ValueError(f"generated labels failed schema validation: {errors[0].message}")
    return result


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
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
            yield value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = [label_conversation(record) for record in read_jsonl(args.input)]
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    print(json.dumps({"source_kind": "synthetic_template", "label_kind": "deterministic_rule_silver", "records": len(records)}))


if __name__ == "__main__":
    main()
