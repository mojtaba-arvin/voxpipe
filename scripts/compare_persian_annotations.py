#!/usr/bin/env python3
"""Compare two independent Persian annotation exports before adjudication.

Reports exact claim-value precision/recall/F1 and evidence-span agreement.
This is a pairwise agreement diagnostic, not Cohen's kappa and not a gold score.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

COLLECTIONS = ("entities", "events", "topics", "relations", "action_items")
_REQUIRED_VALUE_FIELDS = {
    "entities": {"name", "entity_type"},
    "events": {"name", "event_type"},
    "topics": {"name"},
    "relations": {"subject", "predicate", "object"},
    "action_items": {"description"},
}
_ALLOWED_VALUE_FIELDS = {
    "entities": {"name", "entity_type", "aliases", "description"},
    "events": {"name", "event_type", "start_time", "end_time", "location", "participants"},
    "topics": {"name", "description", "keywords"},
    "relations": {"subject", "predicate", "object"},
    "action_items": {"description", "assignee", "due_date", "status"},
}


class AnnotationError(ValueError):
    """An annotator export is malformed or the two exports are not comparable."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_rows(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AnnotationError(f"{path}: cannot read UTF-8 JSONL: {exc}") from exc

    rows: dict[str, dict[str, Any]] = {}
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            raise AnnotationError(f"{path}:{line_no}: blank lines are not allowed")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnnotationError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise AnnotationError(f"{path}:{line_no}: each row must be an object")
        sample_id = row.get("sample_id")
        segments = row.get("transcript_segments")
        annotations = row.get("annotations")
        if not isinstance(sample_id, str) or not sample_id:
            raise AnnotationError(f"{path}:{line_no}: sample_id must be a non-empty string")
        if sample_id in rows:
            raise AnnotationError(f"{path}:{line_no}: duplicate sample_id {sample_id!r}")
        if not isinstance(segments, dict) or not segments or any(
            not isinstance(key, str) or not key or not isinstance(text, str)
            for key, text in segments.items()
        ):
            raise AnnotationError(f"{path}:{line_no}: transcript_segments must map IDs to text")
        if not isinstance(annotations, dict) or set(annotations) != set(COLLECTIONS):
            raise AnnotationError(f"{path}:{line_no}: annotations must contain exactly {COLLECTIONS}")

        seen: set[tuple[str, str]] = set()
        for collection in COLLECTIONS:
            claims = annotations[collection]
            if not isinstance(claims, list):
                raise AnnotationError(f"{path}:{line_no}: {collection} must be an array")
            for claim_index, claim in enumerate(claims):
                prefix = f"{path}:{line_no}: {collection}[{claim_index}]"
                if not isinstance(claim, dict) or not isinstance(claim.get("value"), dict):
                    raise AnnotationError(f"{prefix}: value must be an object")
                value = claim["value"]
                required = _REQUIRED_VALUE_FIELDS[collection]
                allowed = _ALLOWED_VALUE_FIELDS[collection]
                if not required.issubset(value) or set(value) - allowed:
                    raise AnnotationError(f"{prefix}: value fields do not match the {collection} contract")
                if any(not isinstance(value[field], str) or not value[field].strip() for field in required):
                    raise AnnotationError(f"{prefix}: required value fields must be non-empty strings")
                if collection == "entities" and value["entity_type"] not in {
                    "person", "organization", "location", "document"
                }:
                    raise AnnotationError(f"{prefix}: unsupported entity_type")
                key = (collection, canonical(value))
                if key in seen:
                    raise AnnotationError(f"{prefix}: duplicate claim value")
                seen.add(key)
                evidence = claim.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise AnnotationError(f"{prefix}: evidence must be a non-empty array")
                for evidence_index, span in enumerate(evidence):
                    ep = f"{prefix}.evidence[{evidence_index}]"
                    if not isinstance(span, dict):
                        raise AnnotationError(f"{ep}: evidence must be an object")
                    segment_id = span.get("segment_id")
                    start, end, quote = span.get("start_char"), span.get("end_char"), span.get("quote")
                    if not isinstance(segment_id, str) or segment_id not in segments:
                        raise AnnotationError(f"{ep}: unknown segment_id")
                    if (not isinstance(start, int) or isinstance(start, bool)
                            or not isinstance(end, int) or isinstance(end, bool)
                            or not isinstance(quote, str) or end <= start
                            or end > len(segments[segment_id])
                            or segments[segment_id][start:end] != quote):
                        raise AnnotationError(f"{ep}: evidence does not match exact Unicode offsets")
        rows[sample_id] = row
    return rows


def score(tp: int, left_only: int, right_only: int) -> dict[str, int | float | None]:
    precision = tp / (tp + left_only) if tp + left_only else (None if not right_only else 0.0)
    recall = tp / (tp + right_only) if tp + right_only else (None if not left_only else 0.0)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else (0.0 if precision == 0.0 or recall == 0.0 else None)
    )
    return {"matched": tp, "annotator_a_only": left_only, "annotator_b_only": right_only,
            "precision_a_to_b": precision, "recall_b_to_a": recall, "f1": f1}


def find_disagreements(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic, transcript-free claim/evidence disagreements for adjudication."""
    missing_right = sorted(set(left) - set(right))
    missing_left = sorted(set(right) - set(left))
    if missing_right or missing_left:
        raise AnnotationError(
            f"sample coverage differs (missing from annotator B={missing_right[:10]}, "
            f"missing from annotator A={missing_left[:10]})"
        )

    rows: list[dict[str, Any]] = []
    for sample_id in sorted(left):
        a, b = left[sample_id], right[sample_id]
        if a["transcript_segments"] != b["transcript_segments"]:
            raise AnnotationError(f"sample {sample_id!r}: transcript source differs between annotators")
        for collection in COLLECTIONS:
            a_claims = {canonical(item["value"]): item for item in a["annotations"][collection]}
            b_claims = {canonical(item["value"]): item for item in b["annotations"][collection]}
            for key in sorted(set(a_claims) | set(b_claims)):
                claim_a, claim_b = a_claims.get(key), b_claims.get(key)
                evidence_a = sorted(claim_a["evidence"], key=canonical) if claim_a else []
                evidence_b = sorted(claim_b["evidence"], key=canonical) if claim_b else []
                if claim_a is None:
                    kind = "annotator_b_only"
                elif claim_b is None:
                    kind = "annotator_a_only"
                elif [canonical(x) for x in evidence_a] != [canonical(x) for x in evidence_b]:
                    kind = "evidence_mismatch"
                else:
                    continue
                value = json.loads(key)
                rows.append({
                    "sample_id": sample_id,
                    "category": collection,
                    "disagreement_type": kind,
                    "claim_value": value,
                    "annotator_a": {"present": claim_a is not None, "evidence": evidence_a},
                    "annotator_b": {"present": claim_b is not None, "evidence": evidence_b},
                })
    return rows


def write_disagreements(path: Path, rows: list[dict[str, Any]], *, input_paths: tuple[Path, ...] = ()) -> None:
    """Atomically write a JSONL adjudication queue without overwriting source exports."""
    target = path.resolve()
    if target in {source.resolve() for source in input_paths}:
        raise AnnotationError("disagreement output must not overwrite an annotator input")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise AnnotationError(f"{path}: cannot write disagreement queue: {exc}") from exc


def compare(left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing_right = sorted(set(left) - set(right))
    missing_left = sorted(set(right) - set(left))
    if missing_right or missing_left:
        raise AnnotationError(
            f"sample coverage differs (missing from annotator B={missing_right[:10]}, "
            f"missing from annotator A={missing_left[:10]})"
        )

    totals = {name: [0, 0, 0] for name in COLLECTIONS}
    evidence_matches = evidence_compared = 0
    for sample_id in sorted(left):
        a, b = left[sample_id], right[sample_id]
        if a["transcript_segments"] != b["transcript_segments"]:
            raise AnnotationError(f"sample {sample_id!r}: transcript source differs between annotators")
        for collection in COLLECTIONS:
            a_claims = {canonical(x["value"]): x for x in a["annotations"][collection]}
            b_claims = {canonical(x["value"]): x for x in b["annotations"][collection]}
            common = set(a_claims) & set(b_claims)
            totals[collection][0] += len(common)
            totals[collection][1] += len(set(a_claims) - set(b_claims))
            totals[collection][2] += len(set(b_claims) - set(a_claims))
            for key in common:
                evidence_compared += 1
                a_evidence = sorted(canonical(span) for span in a_claims[key]["evidence"])
                b_evidence = sorted(canonical(span) for span in b_claims[key]["evidence"])
                evidence_matches += int(a_evidence == b_evidence)

    per_type = {
        name: score(*totals[name])
        for name in COLLECTIONS
    }
    aggregate = [sum(totals[name][index] for name in COLLECTIONS) for index in range(3)]
    return {
        "metric": "pairwise-exact-claim-agreement-v1",
        "samples_compared": len(left),
        "per_type": per_type,
        "micro": score(*aggregate),
        "evidence": {
            "matched_value_claims_compared": evidence_compared,
            "exact_evidence_matches": evidence_matches,
            "exact_evidence_match_rate": evidence_matches / evidence_compared if evidence_compared else None,
        },
        "interpretation": (
            "Pairwise exact-value agreement only; this is not Cohen's kappa, does not model true negatives, "
            "does not establish semantic correctness, and does not replace independent adjudication."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotator-a", required=True, type=Path)
    parser.add_argument("--annotator-b", required=True, type=Path)
    parser.add_argument("--disagreements-output", type=Path,
                        help="optional JSONL adjudication queue; never overwrites input exports")
    args = parser.parse_args()
    try:
        left, right = read_rows(args.annotator_a), read_rows(args.annotator_b)
        report = compare(left, right)
        if args.disagreements_output is not None:
            queue = find_disagreements(left, right)
            write_disagreements(args.disagreements_output, queue,
                                input_paths=(args.annotator_a, args.annotator_b))
            report["disagreements"] = {"count": len(queue), "output": str(args.disagreements_output)}
    except AnnotationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
