"""Score existing STT JSONL results without loading a model or calling a service."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def normalize(text: str) -> list[str]:
    text = text.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"}))
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return text.casefold().split()


def edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for i, token in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (token != other)))
        previous = current
    return previous[-1]


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def score(rows: list[dict]) -> dict:
    ref_words = hyp_words = word_errors = char_errors = ref_chars = 0
    audio_seconds = inference_seconds = estimated_cost = 0.0
    cost_known = True
    for row in rows:
        reference, hypothesis = normalize(row["reference"]), normalize(row["hypothesis"])
        word_errors += edit_distance(reference, hypothesis)
        ref_words += len(reference)
        hyp_words += len(hypothesis)
        ref_text, hyp_text = " ".join(reference), " ".join(hypothesis)
        char_errors += edit_distance(list(ref_text), list(hyp_text))
        ref_chars += len(ref_text)
        audio, inference = float(row["audio_duration_seconds"]), float(row["inference_seconds"])
        if audio <= 0 or inference < 0:
            raise ValueError("audio duration must be positive and inference time non-negative")
        audio_seconds += audio
        inference_seconds += inference
        rate = row.get("cost_per_audio_minute")
        if rate is None:
            cost_known = False
        else:
            estimated_cost += audio / 60 * float(rate)
    return {
        "files": len(rows), "reference_words": ref_words, "hypothesis_words": hyp_words,
        "wer": word_errors / ref_words if ref_words else None,
        "cer": char_errors / ref_chars if ref_chars else None,
        "real_time_factor": inference_seconds / audio_seconds if audio_seconds else None,
        "audio_seconds": audio_seconds, "inference_seconds": inference_seconds,
        "estimated_api_cost_usd": estimated_cost if cost_known else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="JSONL results from a consented corpus")
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows:
            raise ValueError("benchmark file contains no result rows")
        required = {"reference", "hypothesis", "audio_duration_seconds", "inference_seconds"}
        for number, row in enumerate(rows, 1):
            if not isinstance(row, dict) or not required.issubset(row):
                raise ValueError(f"row {number} is missing required fields: {sorted(required)}")
        print(json.dumps(score(rows), ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
