"""Deterministic rule-based silver labels for synthetic Persian conversations."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "eval" / "artifacts" / "issue32" / "utterances.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue32" / "silver_labels.jsonl"
DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

DATE_RE = re.compile(r"(?<!\d)(14\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?!\d)")
AMOUNT_RE = re.compile(r"(?:مبلغ|بازپرداخت)\s*([\d,٬]+)\s*تومان")
ORDER_RE = re.compile(r"\bORD-\d{4}\b")
NAME_RE = re.compile(r"مشتری نمونه\s+\d{3}")


def normalize_digits(text: str) -> str:
    return text.translate(DIGIT_TRANSLATION)


def _extract_one(pattern: re.Pattern[str], text: str, normalize: Any = None) -> dict[str, Any]:
    matches = [match.group(0) for match in pattern.finditer(text)]
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
    text = normalize_digits("\n".join(item["text"] for item in record["utterances"]))
    fields = {
        "date": _extract_one(DATE_RE, text),
        "amount": _extract_one(AMOUNT_RE, text, _normalize_amount),
        "order_id": _extract_one(ORDER_RE, text),
        "customer_name": _extract_one(NAME_RE, text),
    }
    return {
        "conversation_id": record["conversation_id"],
        "source_kind": record["source_kind"],
        "label_kind": "deterministic_rule_silver",
        "fields": fields,
    }


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
