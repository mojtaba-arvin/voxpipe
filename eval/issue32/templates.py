"""Deterministic Persian synthetic conversation generator for extraction evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "eval" / "schemas" / "conversation.schema.json"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue32" / "utterances.jsonl"
DEFAULT_METADATA = ROOT / "eval" / "artifacts" / "issue32" / "metadata_32.json"
SCENARIOS = ("order_confirmation", "address_change", "refund", "callback")
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _persian_digits(value: str) -> str:
    return value.translate(PERSIAN_DIGITS)


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def generate_conversations(count: int = 120, seed: int = 20261125) -> list[dict[str, Any]]:
    """Generate reproducible, fully synthetic Persian sales/support conversations."""
    if not 100 <= count <= 200:
        raise ValueError("count must be between 100 and 200")
    if seed < 0:
        raise ValueError("seed must be non-negative")

    rng = random.Random(seed)
    records: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        name = f"مشتری نمونه {index:03d}"
        phone = f"PHONE-PLACEHOLDER-{index:03d}"
        order_id = f"ORD-{1000 + index:04d}"
        amount = str(rng.randrange(5, 91) * 10_000)
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        date = f"1405/{month:02d}/{day:02d}"
        scenario = SCENARIOS[(index - 1) % len(SCENARIOS)]
        amount_text = _persian_digits(f"{int(amount):,}").replace(",", "٬")
        date_text = _persian_digits(date)

        if scenario == "order_confirmation":
            customer_text = (
                f"سلام، من {name} هستم. سفارش {order_id} را با مبلغ "
                f"{amount_text} تومان برای تاریخ {date_text} ثبت کرده‌ام. "
                f"شماره تماس من {phone} است."
            )
            agent_text = (
                f"کارشناس: ثبت سفارش {order_id} برای {name} با مبلغ "
                f"{amount_text} تومان و تاریخ {date_text} تأیید شد."
            )
        elif scenario == "address_change":
            customer_text = (
                f"لطفاً نشانی سفارش {order_id} متعلق به {name} را تغییر دهید. "
                f"مبلغ سفارش {amount_text} تومان است. تاریخ ثبت {date_text} "
                f"و شماره تماس {phone} است."
            )
            agent_text = (
                f"کارشناس: درخواست تغییر نشانی سفارش {order_id} برای {name} "
                f"در تاریخ {date_text} ثبت شد."
            )
        elif scenario == "refund":
            customer_text = (
                f"برای سفارش {order_id} به نام {name}، مبلغ {amount_text} "
                f"تومان بازپرداخت شود. درخواست در تاریخ {date_text} ثبت شده "
                f"و شماره تماس {phone} است."
            )
            agent_text = (
                f"کارشناس: مبلغ {amount_text} تومان برای سفارش {order_id} "
                f"در تاریخ {date_text} بررسی می‌شود."
            )
        else:
            customer_text = (
                f"من {name} هستم و برای سفارش {order_id} درخواست تماس مجدد دارم. "
                f"مبلغ سفارش {amount_text} تومان و تاریخ مناسب {date_text} است. "
                f"شماره تماس {phone}."
            )
            agent_text = (
                f"کارشناس: تماس مجدد با {name} درباره سفارش {order_id} "
                f"برای تاریخ {date_text} ثبت شد."
            )

        records.append(
            {
                "conversation_id": f"conversation-{index:04d}",
                "language": "fa-IR",
                "source_kind": "synthetic_template",
                "split": "train",
                "seed": seed,
                "reference_slots": {
                    "customer_name": name,
                    "phone": phone,
                    "order_id": order_id,
                    "amount": amount,
                    "date": date,
                },
                "utterances": [
                    {
                        "utterance_id": f"conversation-{index:04d}-u1",
                        "speaker_id": "customer",
                        "text": customer_text,
                    },
                    {
                        "utterance_id": f"conversation-{index:04d}-u2",
                        "speaker_id": "agent",
                        "text": agent_text,
                    },
                ],
                "scenario": scenario,
            }
        )

    # Keep document-level splits deterministic and disjoint.
    rng.shuffle(records)
    train_end = int(count * 0.70)
    dev_end = train_end + int(count * 0.15)
    for position, record in enumerate(records):
        record["split"] = "train" if position < train_end else "dev" if position < dev_end else "test"

    validator = _validator()
    for record in records:
        # scenario is generator metadata, not part of the canonical conversation contract.
        scenario = record.pop("scenario")
        errors = list(validator.iter_errors(record))
        record["scenario"] = scenario
        if errors:
            raise ValueError(f"generated record {record['conversation_id']} failed schema validation: {errors[0].message}")
    return records


def write_dataset(
    records: list[dict[str, Any]],
    output_path: Path = DEFAULT_OUTPUT,
    metadata_path: Path = DEFAULT_METADATA,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    output_path.write_text(payload, encoding="utf-8")
    counts = Counter(record["scenario"] for record in records)
    splits = Counter(record["split"] for record in records)
    metadata = {
        "schema_version": 1,
        "dataset_classification": "synthetic_template_not_natural_conversation",
        "source_kind": "synthetic_template",
        "seed": records[0]["seed"] if records else None,
        "conversation_count": len(records),
        "scenario_counts": dict(sorted(counts.items())),
        "split_counts": dict(sorted(splits.items())),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "output": output_path.name,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20261125)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    args = parser.parse_args()
    records = generate_conversations(args.count, args.seed)
    metadata = write_dataset(records, args.output, args.metadata)
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
