from __future__ import annotations

import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from eval.issue47.common_voice_sample import (
    DATASET_ID,
    build_report,
    _inspect_sample_archive,
    _validate_dataset_details,
    run_download,
)


class CommonVoiceSampleTests(unittest.TestCase):
    def test_dataset_identity_and_license_are_fail_closed(self) -> None:
        details = {
            "id": DATASET_ID,
            "name": "Common Voice Scripted Speech 27.0 - Persian",
            "locale": "fa",
            "task": "ASR",
            "licenseAbbreviation": "CC0-1.0",
            "sizeBytes": 1234,
            "checksum": "abc",
        }
        metadata = _validate_dataset_details(details)
        self.assertEqual(metadata["dataset_archive_size_bytes"], 1234)
        details["licenseAbbreviation"] = "CC-BY-4.0"
        with self.assertRaisesRegex(ValueError, "license"):
            _validate_dataset_details(details)

    def test_sample_archive_counts_audio_without_extracting_or_reading_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "sample.tar.gz"
            audio = Path(temporary) / "clip.mp3"
            audio.write_bytes(b"not-decoded-audio")
            metadata = Path(temporary) / "validated.tsv"
            metadata.write_text(
                "client_id\tpath\tsentence\nprivate\tclip.mp3\ttext\n",
                encoding="utf-8",
            )
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(audio, arcname="clips/clip.mp3")
                archive.add(metadata, arcname="validated.tsv")
            size, audio_count, metadata_count = _inspect_sample_archive(archive_path)
            self.assertEqual(size, archive_path.stat().st_size)
            self.assertEqual(audio_count, 1)
            self.assertEqual(metadata_count, 1)

    def test_report_is_schema_valid_and_discloses_read_speech_boundary(self) -> None:
        report = build_report(
            status="downloaded",
            dataset_archive_size_bytes=1000,
            dataset_archive_checksum="abc",
            sample_archive_name="sample.tar.gz",
            sample_archive_size_bytes=100,
            sample_audio_file_count=5,
            sample_metadata_file_count=1,
            sample_download_seconds=0.25,
        )
        self.assertEqual(report["dataset_version"], "cv-corpus-27.0-2026-09-11")
        self.assertEqual(report["speaker_count"], 4668)
        self.assertTrue(any("not natural conversation" in item for item in report["warnings"]))
        serialized = json.dumps(report).lower()
        self.assertNotIn("client_id", serialized)
        self.assertNotIn("speaker_id", serialized)

    def test_sample_unavailable_does_not_fall_back_to_full_dataset(self) -> None:
        details = {
            "id": DATASET_ID,
            "name": "Common Voice Scripted Speech 27.0 - Persian",
            "locale": "fa",
            "task": "ASR",
            "licenseAbbreviation": "CC0-1.0",
            "sizeBytes": None,
            "checksum": None,
        }
        sample_download = Mock(side_effect=FileNotFoundError)
        sdk = SimpleNamespace(
            download_sample=sample_download,
            get_dataset_details=Mock(return_value=details),
        )
        with patch.dict(sys.modules, {"datacollective": sdk}):
            with tempfile.TemporaryDirectory() as temporary:
                report_path = Path(temporary) / "report.json"
                report = run_download(report_path)
                self.assertEqual(report["status"], "sample_unavailable")
                self.assertTrue(report_path.is_file())
                sample_download.assert_called_once()
                self.assertEqual(sample_download.call_args.kwargs["extract"], False)

    def test_access_denied_is_reported_without_failing_repository_validation(self) -> None:
        details = {
            "id": DATASET_ID,
            "name": "Common Voice Scripted Speech 27.0 - Persian",
            "locale": "fa",
            "task": "ASR",
            "licenseAbbreviation": "CC0-1.0",
            "sizeBytes": None,
            "checksum": None,
        }
        sdk = SimpleNamespace(
            download_sample=Mock(side_effect=PermissionError("403")),
            get_dataset_details=Mock(return_value=details),
        )
        with patch.dict(sys.modules, {"datacollective": sdk}):
            with tempfile.TemporaryDirectory() as temporary:
                report_path = Path(temporary) / "report.json"
                report = run_download(report_path)
                self.assertEqual(report["status"], "access_denied")
                self.assertTrue(report_path.is_file())
                self.assertTrue(any("HTTP 403" in item for item in report["warnings"]))
                sdk.download_sample.assert_called_once()


if __name__ == "__main__":
    unittest.main()
