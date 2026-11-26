"""Download only the publisher-provided Common Voice sample; never persist dataset content."""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DATASET_ID = "cmu5xazgy00finq074vaqo1b3"
DATASET_NAME = "Common Voice Scripted Speech 27.0 - Persian"
DATASET_VERSION = "cv-corpus-27.0-2026-09-11"
RELEASE_DATE = "2026-09-17"
DATASET_URL = f"https://mozilladatacollective.com/datasets/{DATASET_ID}"
DEFAULT_OUTPUT = ROOT / "eval" / "artifacts" / "issue47" / "common_voice_sample.json"
OUTPUT_SCHEMA = ROOT / "eval" / "issue47" / "common_voice_sample.schema.json"
MAX_SAMPLE_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_SAMPLE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_SAMPLE_MEMBERS = 50_000
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


def _sdk_version() -> str:
    try:
        return version("datacollective")
    except PackageNotFoundError:
        return "not-installed"


def _value(details: Any, key: str) -> Any:
    if isinstance(details, dict):
        return details.get(key)
    return getattr(details, key, None)


def _validate_dataset_details(details: Any) -> dict[str, Any]:
    actual = {
        "id": _value(details, "id"),
        "locale": _value(details, "locale"),
        "task": _value(details, "task"),
        "license": _value(details, "licenseAbbreviation"),
        "name": _value(details, "name"),
    }
    expected = {
        "id": DATASET_ID,
        "locale": "fa",
        "task": "ASR",
        "license": "CC0-1.0",
        "name": DATASET_NAME,
    }
    mismatches = [key for key, value in expected.items() if actual[key] != value]
    if mismatches:
        raise ValueError(f"Mozilla dataset metadata mismatch for: {', '.join(mismatches)}")
    return {
        "dataset_archive_size_bytes": _value(details, "sizeBytes"),
        "dataset_archive_checksum": _value(details, "checksum"),
    }


def _inspect_sample_archive(archive_path: Path) -> tuple[int, int, int]:
    if not archive_path.is_file():
        raise ValueError("MDC SDK did not return a sample archive file")
    archive_size = archive_path.stat().st_size
    if archive_size <= 0 or archive_size > MAX_SAMPLE_ARCHIVE_BYTES:
        raise ValueError("publisher sample archive is empty or exceeds the 256 MiB safety limit")

    audio_count = 0
    metadata_count = 0
    total_uncompressed = 0
    member_count = 0
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive:
            member_count += 1
            if member_count > MAX_SAMPLE_MEMBERS:
                raise ValueError("publisher sample exceeds the 50,000-member safety limit")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("publisher sample contains unsupported link or device entries")
            if not member.isfile():
                continue
            total_uncompressed += member.size
            if total_uncompressed > MAX_SAMPLE_UNCOMPRESSED_BYTES:
                raise ValueError("publisher sample exceeds the 1 GiB uncompressed safety limit")
            suffix = Path(member.name).suffix.lower()
            if suffix in AUDIO_SUFFIXES:
                audio_count += 1
            elif suffix in {".tsv", ".csv"}:
                metadata_count += 1
    if audio_count < 1:
        raise ValueError("publisher sample archive contains no recognized audio files")
    return archive_size, audio_count, metadata_count


def build_report(
    *,
    status: str,
    dataset_archive_size_bytes: int | None,
    dataset_archive_checksum: str | None,
    sample_archive_name: str | None = None,
    sample_archive_size_bytes: int | None = None,
    sample_audio_file_count: int | None = None,
    sample_metadata_file_count: int | None = None,
    sample_download_seconds: float | None = None,
    sdk_version: str = "0.8.1",
) -> dict[str, Any]:
    if status not in {"downloaded", "sample_unavailable", "access_denied"}:
        raise ValueError("status must be downloaded, sample_unavailable or access_denied")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "dataset_id": DATASET_ID,
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "release_date": RELEASE_DATE,
        "locale": "fa",
        "task": "ASR",
        "license": "CC0-1.0",
        "source_kind": "scripted_read_speech",
        "source_url": DATASET_URL,
        "recorded_hours": 431.44,
        "validated_hours": 373.15,
        "clip_count": 395173,
        "speaker_count": 4668,
        "dataset_archive_size_bytes": dataset_archive_size_bytes,
        "dataset_archive_checksum": dataset_archive_checksum,
        "downloaded_at": datetime.now(UTC).isoformat(),
        "sdk_version": sdk_version,
        "warnings": [
            "Common Voice is scripted read-speech, not natural conversation.",
            "Speaker count is aggregate publisher metadata only; speaker identifiers and per-speaker records are never read or emitted.",
            "Raw audio, transcript rows and the sample archive are temporary and are never uploaded or committed.",
            "The public dataset page reports release-wide totals; sample file counts describe only the publisher-provided excerpt.",
        ],
    }
    if status == "access_denied":
        result["warnings"].append("MDC denied the authenticated sample-download request with HTTP 403; no dataset bytes were downloaded. Check dataset terms and organization access before retrying.")
    if status == "downloaded":
        if (
            not sample_archive_name
            or sample_archive_size_bytes is None
            or sample_audio_file_count is None
            or sample_metadata_file_count is None
            or sample_download_seconds is None
        ):
            raise ValueError("downloaded reports require complete sample archive metadata")
        result.update(
            {
                "sample_archive_name": sample_archive_name,
                "sample_archive_size_bytes": sample_archive_size_bytes,
                "sample_audio_file_count": sample_audio_file_count,
                "sample_metadata_file_count": sample_metadata_file_count,
                "sample_download_seconds": round(sample_download_seconds, 4),
            }
        )
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    return result


def run_download(output_path: Path, download_directory: Path | None = None) -> dict[str, Any]:
    from datacollective import download_sample, get_dataset_details

    details = get_dataset_details(DATASET_ID)
    dataset_metadata = _validate_dataset_details(details)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="voxpipe-common-voice-") as temporary:
        target = download_directory or Path(temporary)
        target.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        try:
            archive_path = Path(
                download_sample(
                    DATASET_ID,
                    download_directory=str(target),
                    show_progress=False,
                    enable_logging=False,
                    extract=False,
                )
            )
        except PermissionError:
            report = build_report(
                status="access_denied",
                **dataset_metadata,
                sdk_version=_sdk_version(),
            )
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
            return report
        except FileNotFoundError:
            report = build_report(
                status="sample_unavailable",
                **dataset_metadata,
                sdk_version=_sdk_version(),
            )
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return report

        download_seconds = time.perf_counter() - started
        archive_size, audio_count, metadata_count = _inspect_sample_archive(archive_path)
        report = build_report(
            status="downloaded",
            **dataset_metadata,
            sample_archive_name=archive_path.name,
            sample_archive_size_bytes=archive_size,
            sample_audio_file_count=audio_count,
            sample_metadata_file_count=metadata_count,
            sample_download_seconds=download_seconds,
            sdk_version=_sdk_version(),
        )
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = run_download(args.output)
    except PermissionError:
        print("MDC sample-download returned HTTP 403. The GitHub secret is present; the SDK maps this response to PermissionError. Check that the MDC account which owns the API key has accepted this dataset's terms and that the dataset is accessible to that same organization. The public dataset-details endpoint does not verify download permission.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Common Voice sample download failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"downloaded", "access_denied"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
