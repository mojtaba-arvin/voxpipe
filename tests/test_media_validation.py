"""Tests for full-file audio decode validation."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voxpipe.media.validation import DecodePolicy, FFmpegDecodeValidator
from voxpipe.media.probe import MediaInspectionError


class FFmpegDecodeValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "sample.wav"
        self.path.write_bytes(b"staged-test-media")
        self.validator = FFmpegDecodeValidator()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("voxpipe.media.validation.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("voxpipe.media.validation.subprocess.run")
    def test_runs_full_decode_without_shell_or_network(self, run, _which) -> None:
        run.return_value = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")
        self.validator.validate(self.path)
        command = run.call_args.args[0]
        self.assertIn("-xerror", command)
        self.assertIn("-err_detect", command)
        self.assertIn("explode", command)
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(command[command.index("-map") + 1], "0:a:0")
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 60.0)

    @patch("voxpipe.media.validation.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("voxpipe.media.validation.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1))
    def test_maps_timeout_to_stable_error(self, _run, _which) -> None:
        with self.assertRaises(MediaInspectionError) as context:
            self.validator.validate(self.path)
        self.assertEqual(context.exception.code, "MEDIA_DECODE_TIMEOUT")

    @patch("voxpipe.media.validation.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("voxpipe.media.validation.subprocess.run")
    def test_maps_decode_failure_without_leaking_stderr(self, run, _which) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=1, stdout="", stderr="private decoder detail"
        )
        with self.assertRaises(MediaInspectionError) as context:
            self.validator.validate(self.path)
        self.assertEqual(context.exception.code, "MEDIA_DECODE_FAILED")
        self.assertNotIn("private decoder detail", str(context.exception))

    @patch("voxpipe.media.validation.subprocess.run")
    def test_rejects_oversized_file_before_launch(self, run) -> None:
        validator = FFmpegDecodeValidator(policy=DecodePolicy(max_file_size_bytes=4))
        with self.assertRaises(MediaInspectionError) as context:
            validator.validate(self.path)
        self.assertEqual(context.exception.code, "MEDIA_FILE_TOO_LARGE")
        run.assert_not_called()

    def make_wave(self, path: Path, frames: int = 16000) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * frames)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_real_ffmpeg_accepts_complete_wave(self) -> None:
        valid = Path(self.temporary.name) / "valid.wav"
        self.make_wave(valid)
        FFmpegDecodeValidator().validate(valid)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_real_ffmpeg_rejects_truncated_wave(self) -> None:
        valid = Path(self.temporary.name) / "source.wav"
        truncated = Path(self.temporary.name) / "truncated.wav"
        self.make_wave(valid, frames=32000)
        data = valid.read_bytes()
        truncated.write_bytes(data[:-16000])
        with self.assertRaises(MediaInspectionError) as context:
            FFmpegDecodeValidator().validate(truncated)
        self.assertEqual(context.exception.code, "MEDIA_DECODE_FAILED")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_real_ffmpeg_rejects_non_media_bytes(self) -> None:
        invalid = Path(self.temporary.name) / "invalid.bin"
        invalid.write_bytes(b"not a media container")
        with self.assertRaises(MediaInspectionError) as context:
            FFmpegDecodeValidator().validate(invalid)
        self.assertEqual(context.exception.code, "MEDIA_DECODE_FAILED")


if __name__ == "__main__":
    unittest.main()
