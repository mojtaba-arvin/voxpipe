"""Download and verify the complete official Common Voice Persian archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DATASET_ID = "cmu5xazgy00finq074vaqo1b3"
DATASET_NAME = "Common Voice Scripted Speech 27.0 - Persian"
DATASET_VERSION = "cv-corpus-27.0-2026-09-11"
RELEASE_DATE = "2026-09-17"
DATASET_URL = f"https://mozilladatacollective.com/datasets/{DATASET_ID}"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "common_voice_archive.json"
OUTPUT_SCHEMA = ROOT / "eval" / "issue47" / "common_voice_archive.schema.json"
MAX_ARCHIVE_BYTES = 12 * 1024 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 500_000
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


def _sdk_version() -> str:
    try:
        return version("datacollective")
    except PackageNotFoundError:
        return "not-installed"


def _value(details: Any, key: str) -> Any:
    return details.get(key) if isinstance(details, dict) else getattr(details, key, None)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_dataset_details(details: Any) -> dict[str, Any]:
    actual = {
        "id": _value(details, "id"),
        "locale": _value(details, "locale"),
        "task": _value(details, "task"),
        "license": _value(details, "licenseAbbreviation"),
        "name": _value(details, "name"),
    }
    expected = {"id": DATASET_ID, "locale": "fa", "task": "ASR", "license": "CC0-1.0", "name": DATASET_NAME}
    mismatches = [key for key, value in expected.items() if actual[key] != value]
    if mismatches:
        raise ValueError(f"Mozilla dataset metadata mismatch for: {', '.join(mismatches)}")
    return {
        "dataset_archive_size_bytes": int(_value(details, "sizeBytes")) if _value(details, "sizeBytes") else None,
        "dataset_archive_checksum": _value(details, "checksum"),
    }


def _inspect_archive(path: Path) -> tuple[int, int, int]:
    size = path.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise ValueError("full Common Voice archive is empty or exceeds the 12 GiB safety limit")
    audio_count = metadata_count = total_uncompressed = member_count = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            member_count += 1
            if member_count > MAX_ARCHIVE_MEMBERS:
                raise ValueError("Common Voice archive exceeds the 500,000-member safety limit")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("Common Voice archive contains unsupported links or device entries")
            if not member.isfile():
                continue
            total_uncompressed += member.size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Common Voice archive exceeds the 128 GiB uncompressed safety limit")
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("Common Voice archive contains an unsafe path")
            if name.suffix.lower() in AUDIO_SUFFIXES:
                audio_count += 1
            elif name.suffix.lower() == ".tsv":
                metadata_count += 1
    if audio_count < 1 or metadata_count < 1:
        raise ValueError("Common Voice archive must contain audio and TSV metadata")
    return size, audio_count, metadata_count


def build_report(*, archive_name: str, archive_size_bytes: int, archive_sha256: str,
                 audio_file_count: int, metadata_file_count: int, download_seconds: float,
                 dataset_archive_size_bytes: int | None, dataset_archive_checksum: str | None) -> dict[str, Any]:
    result = {
        "schema_version": 1, "status": "downloaded", "dataset_id": DATASET_ID,
        "dataset_name": DATASET_NAME, "dataset_version": DATASET_VERSION,
        "release_date": RELEASE_DATE, "locale": "fa", "task": "ASR", "license": "CC0-1.0",
        "source_kind": "scripted_read_speech", "source_url": DATASET_URL,
        "recorded_hours": 431.44, "validated_hours": 373.15, "clip_count": 395173,
        "speaker_count": 4668, "dataset_archive_size_bytes": dataset_archive_size_bytes,
        "dataset_archive_checksum": dataset_archive_checksum, "downloaded_at": datetime.now(UTC).isoformat(),
        "sdk_version": _sdk_version(), "archive_name": archive_name,
        "archive_size_bytes": archive_size_bytes, "archive_sha256": archive_sha256,
        "audio_file_count": audio_file_count, "metadata_file_count": metadata_file_count,
        "download_seconds": round(download_seconds, 4),
        "warnings": [
            "The complete official Common Voice Persian archive was downloaded to job-local temporary storage.",
            "Only 50 selected read-speech clips are used for Qwen/WhisperX evaluation; the full archive is not extracted.",
            "Common Voice is scripted read-speech, not natural conversation or human gold.",
            "Raw audio, transcripts, speaker identifiers and the archive are never uploaded or committed.",
            "Dataset license is CC0-1.0; this does not establish individual consent for natural-conversation use.",
        ],
    }
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    return result


def run_download(output_path: Path, download_directory: Path) -> tuple[dict[str, Any], Path]:
    from datacollective import download_dataset, get_dataset_details

    details = get_dataset_details(DATASET_ID)
    metadata = _validate_dataset_details(details)
    download_directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    archive_path = Path(download_dataset(
        DATASET_ID, download_directory=str(download_directory), show_progress=False,
        enable_logging=False, extract=False, max_download_attempts=5,
    ))
    elapsed = time.perf_counter() - started
    size, audio_count, metadata_count = _inspect_archive(archive_path)
    actual_sha256 = _sha256_file(archive_path)
    expected_size = metadata["dataset_archive_size_bytes"]
    if expected_size is not None and size != expected_size:
        raise ValueError(f"Downloaded archive size mismatch: expected {expected_size}, received {size}")
    expected_checksum = metadata["dataset_archive_checksum"]
    if isinstance(expected_checksum, str) and len(expected_checksum) == 64:
        if actual_sha256.lower() != expected_checksum.lower():
            raise ValueError("Downloaded archive SHA-256 does not match Mozilla metadata")
    report = build_report(
        archive_name=archive_path.name, archive_size_bytes=size, archive_sha256=actual_sha256,
        audio_file_count=audio_count, metadata_file_count=metadata_count, download_seconds=elapsed,
        **metadata,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--download-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        report, archive = run_download(args.output, args.download_directory)
    except PermissionError:
        print("MDC denied full-archive access (HTTP 403). Check the API-key owner's profile, terms acceptance and dataset access approval.", flush=True)
        return 3
    except Exception as exc:
        print(f"Common Voice full-archive download/validation failed: {type(exc).__name__}", flush=True)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"archive={archive.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
