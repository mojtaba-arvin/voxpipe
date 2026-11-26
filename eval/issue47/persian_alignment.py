"""Ephemeral Persian forced alignment for Common Voice read-speech clips.

Only aggregate coverage and pinned runtime provenance are persisted. Audio,
transcripts, hypotheses and per-word alignment intermediates remain temporary.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-persian"
MODEL_REVISION = "234714078a1398a9db88194c5a40fefe6f376dc1"
MODEL_LICENSE = "Apache-2.0"
SAMPLE_RATE = 16_000
MAX_CLIPS = 50
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "alignment_cv.json"
DEFAULT_SPEAKER_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "speaker_segments.json"
OUTPUT_SCHEMA = ROOT / "eval" / "issue47" / "alignment_cv.schema.json"


def _read_manifest(path: Path, max_clips: int) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError("alignment input manifest must be an existing file")
    if not 1 <= max_clips <= MAX_CLIPS:
        raise ValueError(f"max_clips must be between 1 and {MAX_CLIPS}")
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid alignment input JSON on line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"alignment input line {line_number} must be an object")
            audio_path = Path(str(row.get("audio_path") or "")).resolve()
            transcript = str(row.get("transcript") or "").strip()
            if not audio_path.is_file():
                raise ValueError(f"alignment input line {line_number} references a missing audio file")
            if not transcript:
                raise ValueError(f"alignment input line {line_number} has an empty transcript")
            rows.append({"audio_path": str(audio_path), "transcript": transcript})
            if len(rows) > max_clips:
                raise ValueError(f"alignment input exceeds the {max_clips}-clip limit")
    if not rows:
        raise ValueError("alignment input manifest contains no clips")
    return rows


def _validated_word_times(words: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        token = str(word.get("word") or "").strip()
        start, end = word.get("start"), word.get("end")
        if not token or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        if start < 0 or end <= start or end > duration + 0.05:
            continue
        valid.append({"word": token, "start": round(float(start), 4), "end": round(float(end), 4)})
    return valid


def build_report(
    *,
    clip_count: int,
    aligned_clip_count: int,
    failed_clip_count: int,
    word_count: int,
    aligned_word_count: int,
    model_dtype: str,
    whisperx_version: str,
) -> dict[str, Any]:
    if clip_count < 1 or aligned_clip_count < 0 or failed_clip_count < 0:
        raise ValueError("clip counts are invalid")
    if aligned_clip_count + failed_clip_count != clip_count:
        raise ValueError("aligned and failed clip counts must account for every clip")
    if word_count < 1 or not 0 <= aligned_word_count <= word_count:
        raise ValueError("word counts are invalid")
    status = "completed" if failed_clip_count == 0 else ("partial" if aligned_clip_count else "failed")
    report = {
        "schema_version": 1,
        "status": status,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_license": MODEL_LICENSE,
        "whisperx_version": whisperx_version,
        "device": "cpu",
        "model_dtype": model_dtype,
        "sample_rate": SAMPLE_RATE,
        "batch_size": 1,
        "clip_count": clip_count,
        "aligned_clip_count": aligned_clip_count,
        "failed_clip_count": failed_clip_count,
        "word_count": word_count,
        "aligned_word_count": aligned_word_count,
        "alignment_coverage": round(aligned_word_count / word_count, 6),
        "alignment_intermediate_uploaded": False,
        "audio_uploaded": False,
        "transcript_text_uploaded": False,
        "speaker_ids_used": False,
        "diarization_status": "not_evaluated_read_speech",
        "speaker_segment_count": 0,
        "warnings": [
            "Common Voice is scripted read-speech, not natural conversation.",
            "Alignment coverage and timestamp invariants are operational diagnostics, not gold timestamp accuracy.",
            "Word-level alignment intermediates, audio and transcript text are temporary and are never uploaded.",
            "Diarization and overlap are not evaluated on isolated read-speech clips; natural conversational diarization remains M2.",
            "The WhisperX compute_type option configures an ASR backend, not this Wav2Vec2 forced-aligner; actual model dtype is recorded instead.",
        ],
    }
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    return report


def run_alignment(
    manifest_path: Path,
    output_path: Path,
    speaker_output_path: Path,
    cache_dir: Path,
    *,
    max_clips: int = MAX_CLIPS,
    model_loader: Callable[[Path], tuple[Any, dict[str, Any], Callable[..., Any], Callable[..., Any]]] | None = None,
) -> dict[str, Any]:
    rows = _read_manifest(manifest_path, max_clips)
    if model_loader is None:
        import whisperx

        model, metadata = whisperx.load_align_model(
            language_code="fa",
            device="cpu",
            model_name=MODEL_ID,
            model_dir=str(cache_dir),
        )
        align_function = whisperx.align
        audio_loader = whisperx.load_audio
        try:
            whisperx_version = version("whisperx")
        except PackageNotFoundError:
            whisperx_version = "unknown"
    else:
        model, metadata, align_function, audio_loader = model_loader(cache_dir)
        try:
            whisperx_version = version("whisperx")
        except PackageNotFoundError:
            whisperx_version = "injected"

    try:
        import torch
        model_dtype = str(next(model.parameters()).dtype).removeprefix("torch.")
    except (ImportError, AttributeError, StopIteration, TypeError):
        model_dtype = "unknown"

    aligned_clips = failed_clips = total_words = aligned_words = 0
    with tempfile.TemporaryDirectory(prefix="voxpipe-persian-alignment-") as temporary:
        intermediate_path = Path(temporary) / "alignment_cv.jsonl"
        with intermediate_path.open("w", encoding="utf-8") as intermediate:
            for index, row in enumerate(rows, start=1):
                transcript = row["transcript"]
                expected_words = len(transcript.split())
                total_words += expected_words
                try:
                    audio = audio_loader(row["audio_path"])
                    duration = len(audio) / SAMPLE_RATE
                    if not math.isfinite(duration) or duration <= 0:
                        raise ValueError("audio duration must be positive")
                    segments = [{"start": 0.0, "end": duration, "text": transcript}]
                    result = align_function(
                        segments,
                        model,
                        metadata,
                        audio,
                        "cpu",
                        return_char_alignments=False,
                        print_progress=False,
                    )
                    word_rows = [
                        word
                        for segment in result.get("segments", [])
                        for word in segment.get("words", [])
                    ]
                    valid_words = _validated_word_times(word_rows, duration)
                    if not valid_words:
                        raise ValueError("aligner returned no valid word timestamps")
                    aligned_clips += 1
                    aligned_words += min(len(valid_words), expected_words)
                    intermediate.write(json.dumps({"clip": index, "words": valid_words}, ensure_ascii=False) + "\n")
                except Exception:
                    failed_clips += 1

    report = build_report(
        clip_count=len(rows),
        aligned_clip_count=aligned_clips,
        failed_clip_count=failed_clips,
        word_count=total_words,
        aligned_word_count=aligned_words,
        model_dtype=model_dtype,
        whisperx_version=whisperx_version,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    speaker_output_path.parent.mkdir(parents=True, exist_ok=True)
    speaker_output_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "diarization_status": "not_evaluated_read_speech",
                "speaker_segments": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--speaker-output", type=Path, default=DEFAULT_SPEAKER_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "huggingface")
    parser.add_argument("--max-clips", type=int, default=MAX_CLIPS)
    args = parser.parse_args()
    report = run_alignment(
        args.manifest,
        args.output,
        args.speaker_output,
        args.cache_dir,
        max_clips=args.max_clips,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
