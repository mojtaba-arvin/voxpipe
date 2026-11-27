from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from eval.issue47.common_voice_archive import (
    DATASET_ID,
    DATASET_NAME,
    DATASET_VERSION,
    RELEASE_DATE,
    _inspect_archive,
    build_report,
)


class CommonVoiceFullArchiveTests(unittest.TestCase):
    def _archive(self, path: Path, audio_name: str = "clips/clip.mp3") -> None:
        with tarfile.open(path, "w:gz") as archive:
            audio = b"audio-fixture"
            audio_info = tarfile.TarInfo(audio_name)
            audio_info.size = len(audio)
            archive.addfile(audio_info, io.BytesIO(audio))
            metadata = b"client_id\tpath\tsentence\nfixture\tclip.mp3\t\xd8\xb3\xd9\x84\xd8\xa7\xd9\x85\n"
            metadata_info = tarfile.TarInfo("validated.tsv")
            metadata_info.size = len(metadata)
            archive.addfile(metadata_info, io.BytesIO(metadata))

    def test_inspects_full_archive_without_extracting_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "common-voice.tar.gz"
            self._archive(path)
            size, audio_count, metadata_count = _inspect_archive(path)
            self.assertEqual(size, path.stat().st_size)
            self.assertEqual(audio_count, 1)
            self.assertEqual(metadata_count, 1)

    def test_rejects_archive_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.tar.gz"
            self._archive(path, "../escape.mp3")
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                _inspect_archive(path)

    def test_full_archive_report_records_sha256_and_disclosures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "common-voice.tar.gz"
            self._archive(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            report = build_report(
                archive_name=path.name,
                archive_size_bytes=path.stat().st_size,
                archive_sha256=digest,
                audio_file_count=1,
                metadata_file_count=1,
                download_seconds=1.0,
                dataset_archive_size_bytes=path.stat().st_size,
                dataset_archive_checksum=digest,
            )
            self.assertEqual(report["dataset_id"], DATASET_ID)
            self.assertEqual(report["dataset_name"], DATASET_NAME)
            self.assertEqual(report["dataset_version"], DATASET_VERSION)
            self.assertEqual(report["release_date"], RELEASE_DATE)
            self.assertEqual(report["archive_sha256"], digest)
            self.assertTrue(any("not natural conversation" in item for item in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
