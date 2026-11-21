"""Score reproducible STT JSONL results without loading a model or calling a service.

Input is one JSON object per audio file. Keep raw references and hypotheses;
normalization is explicit and versioned in the output so scores remain auditable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

_PERSIAN_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ئ": "ئ", "ك": "ک", "ۀ": "ه",
    "ة": "ه", "ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا",
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})
_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u06d6-\u06ed]")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> list[str]:
    """Return Persian-aware tokens under normalization policy fa-norm-v1.

    Policy: Unicode NFC; Arabic kaf/yeh and alef variants map to Persian forms;
    Arabic/Persian digits map to ASCII; diacritics and tatweel are removed;
    ZWNJ is treated as a token boundary; punctuation is removed; whitespace
    is collapsed. This policy is deliberately explicit, not linguistically
    lossless. Always retain and report raw scores alongside normalized scores.
    """
    if not isinstance(text, str):
        raise TypeError("transcript text must be a string")
    text = unicodedata.normalize("NFC", text).translate(_PERSIAN_TRANSLATION)
    text = text.replace("\u200c", " ").replace("\u0640", "")
    text = _DIACRITICS.sub("", text)
    text = "".join(" " if unicodedata.category(char).startswith("P") else char for char in text)
    text = _WHITESPACE.sub(" ", text).strip().casefold()
    return text.split() if text else []


def normalize_raw(text: str) -> list[str]:
    """Conservative NFC + whitespace normalization, without Persian folding."""
    if not isinstance(text, str):
        raise TypeError("transcript text must be a string")
    value = _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()
    return value.split() if value else []


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


def _rates(rows: list[dict], tokenizer) -> dict:
    ref_words = hyp_words = word_errors = char_errors = ref_chars = 0
    for row in rows:
        reference, hypothesis = tokenizer(row["reference"]), tokenizer(row["hypothesis"])
        word_errors += edit_distance(reference, hypothesis)
        ref_words += len(reference)
        hyp_words += len(hypothesis)
        ref_text, hyp_text = " ".join(reference), " ".join(hypothesis)
        char_errors += edit_distance(list(ref_text), list(hyp_text))
        ref_chars += len(ref_text)
    return {
        "wer": word_errors / ref_words if ref_words else None,
        "cer": char_errors / ref_chars if ref_chars else None,
        "reference_words": ref_words,
        "hypothesis_words": hyp_words,
    }


def score(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("benchmark requires at least one result row")
    raw = _rates(rows, normalize_raw)
    normalized = _rates(rows, normalize)
    audio_seconds = inference_seconds = estimated_cost = 0.0
    cost_known = True
    latencies: list[float] = []
    rtfs: list[float] = []
    for row in rows:
        audio, inference = float(row["audio_duration_seconds"]), float(row["inference_seconds"])
        if audio <= 0 or inference < 0:
            raise ValueError("audio duration must be positive and inference time non-negative")
        audio_seconds += audio
        inference_seconds += inference
        latencies.append(inference)
        rtfs.append(inference / audio)
        rate = row.get("cost_per_audio_minute")
        if rate is None:
            cost_known = False
        else:
            rate = float(rate)
            if rate < 0:
                raise ValueError("cost_per_audio_minute cannot be negative")
            estimated_cost += audio / 60 * rate
    return {
        "files": len(rows),
        "normalization": "fa-norm-v1",
        "raw": raw,
        "normalized": normalized,
        # Compatibility aliases: historical wer/cer are normalized scores.
        "wer": normalized["wer"],
        "cer": normalized["cer"],
        "real_time_factor": inference_seconds / audio_seconds,
        "latency_seconds_p50": percentile(latencies, 0.50),
        "latency_seconds_p95": percentile(latencies, 0.95),
        "per_file_rtf_p50": percentile(rtfs, 0.50),
        "per_file_rtf_p95": percentile(rtfs, 0.95),
        "audio_seconds": audio_seconds,
        "inference_seconds": inference_seconds,
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
            for field in ("reference", "hypothesis"):
                if not isinstance(row[field], str):
                    raise ValueError(f"row {number} field {field} must be a string")
        print(json.dumps(score(rows), ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
