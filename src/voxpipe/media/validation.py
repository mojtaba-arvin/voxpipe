"""Full-file audio decode validation using an isolated FFmpeg process."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from voxpipe.media.probe import MediaInspectionError


@dataclass(frozen=True, slots=True)
class DecodePolicy:
    max_file_size_bytes: int = 512 * 1024 * 1024
    timeout_seconds: float = 60.0
    max_alloc_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes must be greater than zero")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.max_alloc_bytes <= 0:
            raise ValueError("max_alloc_bytes must be greater than zero")


class FFmpegDecodeValidator:
    """Decode the selected audio track fully to a null sink.

    ffprobe is a metadata probe and does not prove that all packets can be
    decoded. This validator is a separate, more expensive integrity gate.
    """

    def __init__(
        self,
        binary: str = "ffmpeg",
        policy: DecodePolicy | None = None,
    ) -> None:
        self._binary = binary
        self._policy = policy or DecodePolicy()

    def validate(self, file_path: str | Path) -> None:
        source = Path(file_path)
        if source.is_symlink():
            raise MediaInspectionError("MEDIA_INVALID_PATH", "Symbolic links are not accepted")

        try:
            resolved = source.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MediaInspectionError("MEDIA_FILE_NOT_FOUND", "Media file does not exist") from None

        if not resolved.is_file():
            raise MediaInspectionError("MEDIA_NOT_A_FILE", "Media input must be a regular file")

        try:
            size_bytes = resolved.stat().st_size
        except OSError:
            raise MediaInspectionError("MEDIA_FILE_UNREADABLE", "Media file cannot be inspected") from None

        if size_bytes < 1:
            raise MediaInspectionError("MEDIA_EMPTY_FILE", "Media file is empty")
        if size_bytes > self._policy.max_file_size_bytes:
            raise MediaInspectionError("MEDIA_FILE_TOO_LARGE", "Media file exceeds the configured size limit")

        executable = shutil.which(self._binary)
        if executable is None:
            raise MediaInspectionError("MEDIA_DECODER_UNAVAILABLE", "Media decoder is unavailable")

        command = [
            executable,
            "-nostdin",
            "-v", "error",
            "-xerror",
            "-err_detect", "explode",
            "-threads", "1",
            "-max_alloc", str(self._policy.max_alloc_bytes),
            "-protocol_whitelist", "file,pipe",
            "-i", str(resolved),
            "-map", "0:a:0",
            "-f", "null",
            "-",
        ]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C",
            "LC_ALL": "C",
            "AV_LOG_FORCE_NOCOLOR": "1",
        }

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._policy.timeout_seconds,
                check=False,
                shell=False,
                env=environment,
                cwd=str(resolved.parent),
            )
        except subprocess.TimeoutExpired:
            raise MediaInspectionError("MEDIA_DECODE_TIMEOUT", "Full media validation exceeded its time limit") from None
        except OSError:
            raise MediaInspectionError("MEDIA_DECODER_UNAVAILABLE", "Media decoder process could not be started") from None

        if result.returncode != 0:
            raise MediaInspectionError("MEDIA_DECODE_FAILED", "Media audio stream could not be fully decoded")
