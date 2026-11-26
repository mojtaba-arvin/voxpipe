"""Aggregate read-speech ASR/alignment and synthetic overlap as separate evidence branches."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "report_47.json"
DEFAULT_MARKDOWN = ROOT / "eval" / "artifacts" / "issue47" / "report_47.md"
SCHEMA_PATH = ROOT / "eval" / "issue47" / "report_47.schema.json"


def _normalise_job_status(value: str) -> str:
    value = value.strip().lower()
    if value == "success":
        return "success"
    if value == "skipped":
        return "skipped"
    return "failure"


def _validate_document(value: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError(f"{label} failed schema validation: {errors[0].message}")


def build_report(
    *,
    read_speech_job_status: str,
    synthetic_job_status: str,
    qwen_report: dict[str, Any] | None,
    alignment_report: dict[str, Any] | None,
    speaker_report: dict[str, Any] | None,
    overlap_manifest: dict[str, Any] | None,
    run_id: int,
    sample_report: dict[str, Any] | None = None,
    commit_sha: str,
    run_url: str,
) -> dict[str, Any]:
    read_status = _normalise_job_status(read_speech_job_status)
    synthetic_status = _normalise_job_status(synthetic_job_status)
    warnings = [
        "Common Voice is scripted read-speech, not natural conversational audio.",
        "Synthetic TTS overlap fixtures are synthetic pipeline evidence, not natural-speech or distinct-speaker diarization evidence.",
        "No medical-readiness or production-quality claim is made by this report.",
    ]

    read_metrics: dict[str, Any] | None = None
    if read_status == "success":
        try:
            if qwen_report is None or alignment_report is None or speaker_report is None or sample_report is None:
                raise ValueError("read-speech job succeeded without all four expected reports")
            _validate_document(sample_report, ROOT / "eval" / "issue47" / "common_voice_sample.schema.json", "Common Voice sample provenance")
            _validate_document(qwen_report, ROOT / "eval" / "issue47" / "qwen3_asr_persian.schema.json", "Qwen ASR report")
            _validate_document(alignment_report, ROOT / "eval" / "issue47" / "alignment_cv.schema.json", "alignment report")
            if sample_report.get("status") != "downloaded":
                raise ValueError("Common Voice bounded sample was not downloaded")
            for key in ("dataset_id", "dataset_version", "source_kind", "locale", "license"):
                if sample_report.get(key) != qwen_report.get(key):
                    raise ValueError(f"sample/Qwen dataset provenance mismatch for {key}")
            if qwen_report.get("status") != "passed":
                raise ValueError("Qwen ASR report is not passed")
            if alignment_report.get("status") != "completed":
                raise ValueError("Persian alignment report is not completed")
            if speaker_report != {
                "schema_version": 1,
                "diarization_status": "not_evaluated_read_speech",
                "speaker_segments": [],
            }:
                raise ValueError("read-speech speaker report must explicitly contain no evaluated speaker segments")
            if qwen_report.get("audio_uploaded") is not False or qwen_report.get("transcript_text_uploaded") is not False:
                raise ValueError("Qwen report indicates private sample content was uploaded")
            if alignment_report.get("audio_uploaded") is not False or alignment_report.get("transcript_text_uploaded") is not False:
                raise ValueError("alignment report indicates private sample content was uploaded")
            read_metrics = {
                "dataset": {
                    "id": qwen_report["dataset_id"],
                    "version": qwen_report["dataset_version"],
                    "source_kind": qwen_report["source_kind"],
                    "locale": qwen_report["locale"],
                    "license": qwen_report["license"],
                    "task": sample_report["task"],
                    "source_url": sample_report["source_url"],
                    "release_date": sample_report["release_date"],
                    "recorded_hours": sample_report["recorded_hours"],
                    "validated_hours": sample_report["validated_hours"],
                    "clip_count": sample_report["clip_count"],
                    "speaker_count": sample_report["speaker_count"],
                    "sample_archive": {
                        "name": sample_report["sample_archive_name"],
                        "size_bytes": sample_report["sample_archive_size_bytes"],
                        "sha256": sample_report["sample_archive_checksum"],
                        "audio_file_count": sample_report["sample_audio_file_count"],
                        "metadata_file_count": sample_report["sample_metadata_file_count"],
                        "download_seconds": sample_report["sample_download_seconds"],
                    },
                },
                "asr_model": {
                    "id": qwen_report["model_id"],
                    "revision": qwen_report["model_revision"],
                    "license": qwen_report["model_license"],
                    "device": qwen_report["device"],
                    "compute_type": qwen_report["compute_type"],
                },
                "sample_count": qwen_report["sample_count"],
                "wer": qwen_report["wer"],
                "cer": qwen_report["cer"],
                "alignment_model": {
                    "id": alignment_report["model_id"],
                    "revision": alignment_report["model_revision"],
                    "license": alignment_report["model_license"],
                    "whisperx_version": alignment_report["whisperx_version"],
                    "device": alignment_report["device"],
                    "model_dtype": alignment_report["model_dtype"],
                    "batch_size": alignment_report["batch_size"],
                },
                "alignment_coverage": alignment_report["alignment_coverage"],
                "aligned_clip_count": alignment_report["aligned_clip_count"],
                "failed_clip_count": alignment_report["failed_clip_count"],
                "diarization_status": "not_evaluated_read_speech",
            }
        except (KeyError, TypeError, ValueError) as exc:
            read_status = "failure"
            warnings.append(f"Read-speech evidence was rejected: {exc}")

    synthetic_metrics: dict[str, Any] | None = None
    if synthetic_status == "success":
        try:
            if overlap_manifest is None:
                raise ValueError("synthetic overlap job succeeded without its manifest")
            _validate_document(
                overlap_manifest,
                ROOT / "eval" / "issue47" / "overlap_manifest.schema.json",
                "synthetic overlap manifest",
            )
            if overlap_manifest.get("audio_artifact_uploaded") is not False:
                raise ValueError("synthetic overlap report indicates audio artifacts were uploaded")
            if overlap_manifest.get("transcript_text_uploaded") is not False:
                raise ValueError("synthetic overlap report indicates transcript text was uploaded")
            synthetic_metrics = {
                "conversation_count": overlap_manifest["conversation_count"],
                "overlap_count": overlap_manifest["overlap_count"],
                "mean_overlap_seconds": overlap_manifest["mean_overlap_seconds"],
                "audio_files_persisted": overlap_manifest["audio_files_persisted"],
                "audio_artifact_uploaded": overlap_manifest["audio_artifact_uploaded"],
            }
        except (KeyError, TypeError, ValueError) as exc:
            synthetic_status = "failure"
            warnings.append(f"Synthetic overlap evidence was rejected: {exc}")

    if read_status == "success" and synthetic_status == "success":
        status = "success"
    elif read_status == "skipped" and synthetic_status == "skipped":
        status = "skipped"
    else:
        status = "failure"

    report = {
        "schema_version": 1,
        "status": status,
        "run_id": run_id,
        "commit_sha": commit_sha,
        "workflow_run_url": run_url,
        "read_speech": {
            "status": read_status,
            "artifact_name": "persian-read-speech-evaluation" if read_speech_job_status == "success" else None,
            "metrics": read_metrics,
        },
        "synthetic_overlap": {
            "status": synthetic_status,
            "artifact_name": "synthetic-overlap-annotations" if synthetic_job_status == "success" else None,
            "metrics": synthetic_metrics,
        },
        "natural_conversation_evidence": False,
        "medical_readiness_claim": False,
        "warnings": warnings,
    }
    _validate_document(report, SCHEMA_PATH, "speech aggregate report")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    read = report["read_speech"]
    synthetic = report["synthetic_overlap"]
    lines = [
        "## Group C — speech evaluation aggregation",
        "",
        f"- Overall group status: **{report['status']}**",
        f"- Read-speech ASR/alignment: **{read['status']}**",
        f"- Synthetic overlap: **{synthetic['status']}**",
        f"- Run: [#{report['run_id']}]({report['workflow_run_url']})",
        "",
    ]
    if read["metrics"]:
        metrics = read["metrics"]
        lines.extend([
            f"- Dataset: {metrics['dataset']['id']} ({metrics['dataset']['version']}, {metrics['dataset']['license']})",
            f"- Bounded sample: {metrics['dataset']['sample_archive']['name']} ({metrics['dataset']['sample_archive']['size_bytes']} bytes; {metrics['dataset']['sample_archive']['audio_file_count']} audio files; SHA-256 {metrics['dataset']['sample_archive']['sha256'] or 'not supplied'})",
            f"- ASR model: {metrics['asr_model']['id']}@{metrics['asr_model']['revision']} ({metrics['asr_model']['device']}, {metrics['asr_model']['compute_type']})",
            f"- Persian read-speech clips: {metrics['sample_count']}",
            f"- Qwen WER / CER: {metrics['wer']:.6f} / {metrics['cer']:.6f}",
            f"- Alignment model: {metrics['alignment_model']['id']}@{metrics['alignment_model']['revision']} (WhisperX {metrics['alignment_model']['whisperx_version']}, {metrics['alignment_model']['model_dtype']}, batch {metrics['alignment_model']['batch_size']})",
            f"- Word alignment coverage: {metrics['alignment_coverage']:.6f}",
            f"- Aligned / failed clips: {metrics['aligned_clip_count']} / {metrics['failed_clip_count']}",
            f"- Diarization: {metrics['diarization_status']}",
        ])
    if synthetic["metrics"]:
        metrics = synthetic["metrics"]
        lines.extend([
            f"- Synthetic conversations / overlaps: {metrics['conversation_count']} / {metrics['overlap_count']}",
            f"- Mean synthetic overlap: {metrics['mean_overlap_seconds']:.3f}s",
            "- Generated audio persisted/uploaded: no",
        ])
    lines.extend(["", "**Evidence boundary:** not natural conversational audio; no medical-readiness or production-quality claim."])
    lines.extend(["", "### Disclosures", *[f"- {warning}" for warning in report["warnings"]]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-speech-status", required=True)
    parser.add_argument("--synthetic-status", required=True)
    parser.add_argument("--read-speech-dir", type=Path, required=True)
    parser.add_argument("--synthetic-dir", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    def load(directory: Path, name: str) -> dict[str, Any] | None:
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    report = build_report(
        read_speech_job_status=args.read_speech_status,
        synthetic_job_status=args.synthetic_status,
        qwen_report=load(args.read_speech_dir, "qwen3_asr_persian.json"),
        alignment_report=load(args.read_speech_dir, "alignment_cv.json"),
        speaker_report=load(args.read_speech_dir, "speaker_segments.json"),
        overlap_manifest=load(args.synthetic_dir, "overlap_manifest.json"),
        sample_report=load(args.read_speech_dir, "common_voice_sample.json"),
        run_id=args.run_id,
        commit_sha=args.commit_sha,
        run_url=args.run_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as stream:
            stream.write(f"group_status={report['status']}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
