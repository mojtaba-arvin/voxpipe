"""Isolated ffprobe adapter for authoritative media metadata inspection.

This module performs metadata probing only. It does not decode, transcode,
normalize, or persist media.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MediaInspectionError(Exception):
    """Stable, client-safe media inspection failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ProbePolicy:
    """Resource and acceptance limits applied before/after probing."""

    max_file_size_bytes: int = 512 * 1024 * 1024
    max_duration_ms: int = 4 * 60 * 60 * 1000
    min_sample_rate_hz: int = 8000
    max_sample_rate_hz: int = 384000
    max_channels: int = 32
    probe_size_bytes: int = 5 * 1024 * 1024
    analyze_duration_us: int = 5_000_000
    timeout_seconds: float = 10.0
    max_json_bytes: int = 64 * 1024
    allowed_container_formats: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"wav", "mp3", "m4a", "mp4", "ogg", "flac", "webm", "aac", "amr"}
        )
    )

    def __post_init__(self) -> None:
        positive = (
            "max_file_size_bytes",
            "max_duration_ms",
            "min_sample_rate_hz",
            "max_sample_rate_hz",
            "max_channels",
            "probe_size_bytes",
            "analyze_duration_us",
            "max_json_bytes",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.min_sample_rate_hz > self.max_sample_rate_hz:
            raise ValueError("minimum sample rate cannot exceed maximum sample rate")


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Normalized metadata derived from the actual media file."""

    mime_type: str
    container_format: str
    codec: str
    duration_ms: int
    size_bytes: int
    sample_rate_hz: int
    channels: int
    audio_track_index: int
    bit_depth: int | None = None
    channel_layout: str | None = None


class FFprobeMediaInspector:
    """Inspect staged local media through bounded ffprobe subprocesses.

    The caller must stage the upload into a private, immutable working path.
    The path must never be taken directly from an untrusted request.

    Separate one-stream probes keep ffprobe output small and deterministic:
    the first audio stream is inspected, then the first real video stream is
    queried only to distinguish audio-only containers from audio-bearing video.
    """

    def __init__(
        self,
        binary: str = "ffprobe",
        policy: ProbePolicy | None = None,
    ) -> None:
        self._binary = binary
        self._policy = policy or ProbePolicy()

    def inspect(self, file_path: str | Path) -> MediaMetadata:
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
            raise MediaInspectionError("MEDIA_PROBE_UNAVAILABLE", "Media inspection tool is unavailable")

        audio_payload = self._run_probe(
            executable,
            resolved,
            selector="a:0",
            entries=(
                "format=format_name,duration,size:"
                "stream=index,codec_type,codec_name,sample_rate,channels,"
                "channel_layout,bits_per_sample,bits_per_raw_sample,duration"
            ),
        )
        audio_streams = self._streams(audio_payload)
        if not audio_streams:
            raise MediaInspectionError("MEDIA_NO_AUDIO_STREAM", "Media file contains no audio stream")
        audio = audio_streams[0]

        media_format = audio_payload.get("format")
        if not isinstance(media_format, dict):
            raise MediaInspectionError("MEDIA_PROBE_INVALID_OUTPUT", "Media inspection returned incomplete metadata")
        format_name = media_format.get("format_name")
        if not isinstance(format_name, str) or not format_name:
            raise MediaInspectionError("MEDIA_UNSUPPORTED_CONTAINER", "Media container is not recognized")

        # Uppercase V excludes attached pictures and video thumbnails.
        video_payload = self._run_probe(
            executable,
            resolved,
            selector="V:0",
            entries="format=format_name:stream=index,codec_type",
        )
        has_video = bool(self._streams(video_payload))
        container_format, mime_type = self._normalize_container(format_name, has_video)
        if container_format not in self._policy.allowed_container_formats:
            raise MediaInspectionError("MEDIA_UNSUPPORTED_CONTAINER", "Media container is not supported")

        sample_rate = self._integer(audio.get("sample_rate"))
        channels = self._integer(audio.get("channels"))
        track_index = self._integer(audio.get("index"))
        if sample_rate is None or channels is None or track_index is None:
            raise MediaInspectionError("MEDIA_INCOMPLETE_AUDIO_METADATA", "Audio stream metadata is incomplete")
        if not self._policy.min_sample_rate_hz <= sample_rate <= self._policy.max_sample_rate_hz:
            raise MediaInspectionError("MEDIA_SAMPLE_RATE_OUT_OF_RANGE", "Audio sample rate is outside the supported range")
        if not 1 <= channels <= self._policy.max_channels:
            raise MediaInspectionError("MEDIA_CHANNEL_COUNT_OUT_OF_RANGE", "Audio channel count is outside the supported range")

        duration_ms = self._duration_ms(media_format.get("duration"), audio.get("duration"))
        if duration_ms is None:
            raise MediaInspectionError("MEDIA_DURATION_UNAVAILABLE", "Media duration could not be determined")
        if duration_ms > self._policy.max_duration_ms:
            raise MediaInspectionError("MEDIA_DURATION_LIMIT_EXCEEDED", "Media duration exceeds the configured limit")

        codec = audio.get("codec_name")
        if not isinstance(codec, str) or not codec:
            raise MediaInspectionError("MEDIA_CODEC_UNAVAILABLE", "Audio codec could not be determined")

        bit_depth = self._integer(audio.get("bits_per_raw_sample"))
        if bit_depth is None:
            bit_depth = self._integer(audio.get("bits_per_sample"))
        channel_layout = audio.get("channel_layout")
        if not isinstance(channel_layout, str):
            channel_layout = None

        return MediaMetadata(
            mime_type=mime_type,
            container_format=container_format,
            codec=codec,
            duration_ms=duration_ms,
            size_bytes=size_bytes,
            sample_rate_hz=sample_rate,
            channels=channels,
            audio_track_index=track_index,
            bit_depth=bit_depth,
            channel_layout=channel_layout,
        )

    def _run_probe(
        self,
        executable: str,
        file_path: Path,
        *,
        selector: str,
        entries: str,
    ) -> dict[str, Any]:
        command = [
            executable,
            "-v", "error",
            "-probesize", str(self._policy.probe_size_bytes),
            "-analyzeduration", str(self._policy.analyze_duration_us),
            "-max_alloc", str(64 * 1024 * 1024),
            "-protocol_whitelist", "file",
            "-select_streams", selector,
            "-show_format",
            "-show_streams",
            "-show_entries", entries,
            "-of", "json",
            str(file_path),
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
                cwd=str(file_path.parent),
            )
        except subprocess.TimeoutExpired:
            raise MediaInspectionError("MEDIA_PROBE_TIMEOUT", "Media inspection exceeded its time limit") from None
        except OSError:
            raise MediaInspectionError("MEDIA_PROBE_UNAVAILABLE", "Media inspection process could not be started") from None

        if result.returncode != 0:
            raise MediaInspectionError("MEDIA_PROBE_FAILED", "Media file could not be inspected")

        output = result.stdout or ""
        if len(output.encode("utf-8")) > self._policy.max_json_bytes:
            raise MediaInspectionError("MEDIA_PROBE_OUTPUT_TOO_LARGE", "Media inspection output exceeds its limit")

        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            raise MediaInspectionError("MEDIA_PROBE_INVALID_OUTPUT", "Media inspection returned invalid metadata") from None

        if not isinstance(payload, dict):
            raise MediaInspectionError("MEDIA_PROBE_INVALID_OUTPUT", "Media inspection returned invalid metadata")
        return payload

    @staticmethod
    def _streams(payload: dict[str, Any]) -> list[dict[str, Any]]:
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise MediaInspectionError("MEDIA_PROBE_INVALID_OUTPUT", "Media inspection returned incomplete stream metadata")
        return [stream for stream in streams if isinstance(stream, dict)]

    @staticmethod
    def _integer(value: Any, default: int | None = None) -> int | None:
        try:
            if value is None or value == "N/A":
                return default
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _duration_ms(format_duration: Any, stream_duration: Any) -> int | None:
        for value in (stream_duration, format_duration):
            try:
                seconds = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(seconds) and seconds > 0:
                return int(round(seconds * 1000))
        return None

    @staticmethod
    def _normalize_container(format_name: str, has_video: bool) -> tuple[str, str]:
        names = {name.strip().lower() for name in format_name.split(",")}
        if "wav" in names:
            return "wav", "audio/wav"
        if "mp3" in names:
            return "mp3", "audio/mpeg"
        if "flac" in names:
            return "flac", "audio/flac"
        if "ogg" in names:
            return "ogg", "video/ogg" if has_video else "audio/ogg"
        if "aac" in names:
            return "aac", "audio/aac"
        if "amr" in names:
            return "amr", "audio/amr"
        if names.intersection({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}):
            return ("mp4", "video/mp4") if has_video else ("m4a", "audio/mp4")
        if "webm" in names:
            return "webm", "video/webm" if has_video else "audio/webm"
        if "matroska" in names:
            return "other", "video/x-matroska" if has_video else "audio/x-matroska"
        return "other", "video/octet-stream" if has_video else "audio/octet-stream"
