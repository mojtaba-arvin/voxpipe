"""Bounded WhisperX Persian forced alignment over ephemeral Qwen transcript inputs.

Only aggregate alignment coverage and model/runtime provenance are persisted.
Per-utterance word timings are written to a job-local JSONL file and must never
be uploaded as an artifact.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import resource
import time
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from eval.issue47.qwen_transcription import (
    MAX_CLIPS,
    MODEL_REVISION as QWEN_MODEL_REVISION,
    _audio_duration_seconds,
    normalize_text,
)

ROOT = Path(__file__).resolve().parents[2]
ALIGN_MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-persian"
ALIGN_MODEL_REVISION = "234714078a1398a9db88194c5a40fefe6f376dc1"
ALIGN_MODEL_LICENSE = "Apache-2.0"
WHISPERX_VERSION = "3.8.6"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "whisperx_alignment.json"
DEFAULT_SPEAKER_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "speaker_segments.json"
OUTPUT_SCHEMA = ROOT / "eval" / "issue47" / "whisperx_alignment.schema.json"
SPEAKER_SCHEMA = ROOT / "eval" / "issue47" / "speaker_segments.schema.json"


def load_intermediates(path: Path, max_clips: int = MAX_CLIPS) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError("Qwen ephemeral transcript input does not exist")
    if not 1 <= max_clips <= MAX_CLIPS:
        raise ValueError(f"max_clips must be between 1 and {MAX_CLIPS}")
    records: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Qwen intermediate JSONL at line {line_number}") from exc
            sample_id = record.get("sample_id")
            audio_path = record.get("audio_path")
            reference = record.get("reference")
            hypothesis = record.get("hypothesis")
            if not isinstance(sample_id, str) or not sample_id.startswith("sample-"):
                raise ValueError(f"invalid sample identifier at line {line_number}")
            if sample_id in seen_ids:
                raise ValueError("Qwen intermediate JSONL contains duplicate sample identifiers")
            if not isinstance(audio_path, str) or not Path(audio_path).is_file():
                raise ValueError(f"audio path is missing at line {line_number}")
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError(f"reference text is missing at line {line_number}")
            if not isinstance(hypothesis, str) or not hypothesis.strip():
                raise ValueError(f"Qwen hypothesis is missing at line {line_number}")
            seen_ids.add(sample_id)
            records.append({
                "sample_id": sample_id,
                "audio_path": str(Path(audio_path).resolve()),
                "reference": reference,
                "hypothesis": hypothesis,
            })
            if len(records) > max_clips:
                raise ValueError(f"Qwen intermediate JSONL exceeds the {max_clips}-clip limit")
    if not records:
        raise ValueError("Qwen intermediate JSONL contains no usable samples")
    return records


def load_alignment_model(cache_dir: Path) -> tuple[Any, dict[str, Any]]:
    from huggingface_hub import snapshot_download
    import whisperx

    model_dir = Path(snapshot_download(
        repo_id=ALIGN_MODEL_ID,
        revision=ALIGN_MODEL_REVISION,
        cache_dir=str(cache_dir),
    ))
    return whisperx.load_align_model(
        language_code="fa",
        device="cpu",
        model_name=str(model_dir),
        model_dir=str(cache_dir),
        model_cache_only=True,
    )


def _run_whisperx_align(transcript: list[dict[str, Any]], model: Any,
                        metadata: dict[str, Any], audio: str) -> dict[str, Any]:
    import whisperx
    return whisperx.align(
        transcript,
        model,
        metadata,
        audio,
        device="cpu",
        return_char_alignments=False,
    )


def _valid_timestamp(value: Any, duration: float) -> bool:
    return (
        isinstance(value, (int, float))
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= duration + 0.25
    )


def run_alignment(
    intermediates_path: Path,
    output_path: Path,
    speaker_output_path: Path,
    alignment_jsonl_path: Path,
    cache_dir: Path,
    *,
    max_clips: int = MAX_CLIPS,
    model_loader: Callable[[Path], tuple[Any, dict[str, Any]]] | None = None,
    aligner: Callable[[list[dict[str, Any]], Any, dict[str, Any], str], dict[str, Any]] | None = None,
    duration_fn: Callable[[Path], float] = _audio_duration_seconds,
) -> dict[str, Any]:
    records = load_intermediates(intermediates_path, max_clips=max_clips)
    if model_loader is None:
        model_loader = load_alignment_model
    if aligner is None:
        aligner = _run_whisperx_align

    model, metadata = model_loader(cache_dir)
    total_words = aligned_words = aligned_samples = failed_samples = 0
    audio_seconds = alignment_seconds = 0.0
    failure_reasons: Counter[str] = Counter()

    alignment_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with alignment_jsonl_path.open("w", encoding="utf-8") as intermediate_output:
        for record in records:
            audio_path = Path(record["audio_path"])
            duration = duration_fn(audio_path)
            if not math.isfinite(duration) or duration <= 0:
                failed_samples += 1
                failure_reasons["invalid_audio_duration"] += 1
                continue
            expected_words = normalize_text(record["hypothesis"]).split()
            total_words += len(expected_words)
            audio_seconds += duration
            started = time.perf_counter()
            try:
                aligned = aligner(
                    [{"start": 0.0, "end": duration, "text": record["hypothesis"]}],
                    model,
                    metadata,
                    str(audio_path),
                )
                alignment_seconds += time.perf_counter() - started
                segments = aligned.get("segments", []) if isinstance(aligned, dict) else []
                word_results = [
                    word
                    for segment in segments
                    if isinstance(segment, dict)
                    for word in segment.get("words", [])
                    if isinstance(word, dict)
                ]
                safe_words = []
                for word in word_results:
                    start, end = word.get("start"), word.get("end")
                    if (
                        isinstance(word.get("word"), str)
                        and word["word"].strip()
                        and _valid_timestamp(start, duration)
                        and _valid_timestamp(end, duration)
                        and float(end) > float(start)
                    ):
                        safe_words.append({
                            "word": word["word"],
                            "start": round(float(start), 4),
                            "end": round(float(end), 4),
                            "score": round(float(word["score"]), 4)
                            if isinstance(word.get("score"), (int, float))
                            and math.isfinite(float(word["score"])) else None,
                        })
                aligned_words += min(len(safe_words), len(expected_words))
                if safe_words:
                    aligned_samples += 1
                else:
                    failed_samples += 1
                    failure_reasons["no_valid_word_timestamps"] += 1
                intermediate_output.write(json.dumps(
                    {"sample_id": record["sample_id"], "words": safe_words},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n")
            except Exception as exc:
                alignment_seconds += time.perf_counter() - started
                failed_samples += 1
                failure_reasons[type(exc).__name__] += 1
                intermediate_output.write(json.dumps(
                    {"sample_id": record["sample_id"], "words": [], "error_type": type(exc).__name__},
                    separators=(",", ":"),
                ) + "\n")

    coverage = aligned_words / total_words if total_words else 0.0
    status = "passed" if failed_samples == 0 and coverage == 1.0 else (
        "failed" if aligned_words == 0 else "partial"
    )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = int(peak if __import__("sys").platform == "darwin" else peak * 1024)
    try:
        whisperx_version = version("whisperx")
    except PackageNotFoundError:
        whisperx_version = "not-installed"

    report = {
        "schema_version": 1,
        "status": status,
        "locale": "fa",
        "task": "forced_alignment",
        "sample_count": len(records),
        "aligned_sample_count": aligned_samples,
        "failed_sample_count": failed_samples,
        "total_words": total_words,
        "aligned_words": aligned_words,
        "alignment_coverage": round(coverage, 6),
        "audio_duration_seconds": round(audio_seconds, 4),
        "alignment_seconds": round(alignment_seconds, 6),
        "real_time_factor": round(alignment_seconds / audio_seconds, 6) if audio_seconds else 0.0,
        "align_model_id": ALIGN_MODEL_ID,
        "align_model_revision": ALIGN_MODEL_REVISION,
        "align_model_license": ALIGN_MODEL_LICENSE,
        "whisperx_version": whisperx_version,
        "device": "cpu",
        "compute_type": "float32",
        "batch_size": 1,
        "asr_model_revision": QWEN_MODEL_REVISION,
        "peak_rss_bytes": peak_rss_bytes,
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "transcript_text_uploaded": False,
        "word_timestamps_uploaded": False,
        "audio_uploaded": False,
        "speaker_ids_used": False,
        "diarization_status": "not_evaluated_read_speech",
        "warnings": [
            "Common Voice is scripted read-speech, not natural multi-speaker conversation.",
            "No diarization, speaker separation, overlap or participant-identity claims are made.",
            "Per-utterance word timestamps are temporary job-local intermediates and are not uploaded.",
            "Only aggregate alignment coverage and pinned model/runtime provenance are persisted.",
            "WhisperX CLI --compute_type int8 applies to its Faster-Whisper ASR path; this pipeline uses Qwen3-ASR separately and the PyTorch CTC aligner runs in float32.",
        ],
    }
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)

    speaker_report = {
        "schema_version": 1,
        "diarization_status": "not_evaluated_read_speech",
        "source_kind": "scripted_read_speech",
        "speaker_segments": [],
        "speaker_ids_used": False,
        "overlap_claims": False,
        "warning": "Common Voice read-speech is not multi-speaker conversational evidence.",
    }
    speaker_schema = json.loads(SPEAKER_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(speaker_schema)
    Draft202012Validator(speaker_schema).validate(speaker_report)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    speaker_output_path.parent.mkdir(parents=True, exist_ok=True)
    speaker_output_path.write_text(json.dumps(speaker_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intermediates", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--speaker-output", type=Path, default=DEFAULT_SPEAKER_OUTPUT)
    parser.add_argument("--alignment-jsonl", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "huggingface")
    parser.add_argument("--max-clips", type=int, default=MAX_CLIPS)
    args = parser.parse_args()
    report = run_alignment(
        args.intermediates, args.output, args.speaker_output, args.alignment_jsonl,
        args.cache_dir, max_clips=args.max_clips,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
