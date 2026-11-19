"""Canonical speech/transcription contracts and deterministic validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import BinaryIO
from uuid import UUID


_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class TranscriptionErrorCode(StrEnum):
    PROVIDER_UNAVAILABLE = "STT_PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "STT_RATE_LIMITED"
    INVALID_AUDIO = "STT_INVALID_AUDIO"
    UNSUPPORTED_LANGUAGE = "STT_UNSUPPORTED_LANGUAGE"
    AUTHENTICATION_FAILED = "STT_AUTHENTICATION_FAILED"
    RESPONSE_INVALID = "STT_RESPONSE_INVALID"


class TranscriptionError(Exception):
    """Stable provider failure without raw payloads, credentials, or vendor text."""

    def __init__(
        self,
        code: TranscriptionErrorCode,
        *,
        provider: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        if not provider or len(provider) > 128:
            raise ValueError("provider identifier is required and must be bounded")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")
        self.code = code.value
        self.provider = provider
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{self.code} ({provider})")


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    asset_id: str
    audio_stream: BinaryIO
    content_type: str
    language_hint: str | None = None
    word_timestamps: bool = True
    diarization: bool = False
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.asset_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("asset_id must be a UUID") from None
        if not callable(getattr(self.audio_stream, "read", None)):
            raise ValueError("audio_stream must be a readable binary stream")
        readable = getattr(self.audio_stream, "readable", None)
        if callable(readable) and not readable():
            raise ValueError("audio_stream is not readable")
        if not self.content_type.lower().split(";", 1)[0].strip().startswith("audio/"):
            raise ValueError("content_type must identify an extracted audio stream")
        if self.language_hint is not None and not _LANGUAGE_TAG.fullmatch(self.language_hint):
            raise ValueError("language_hint must be a BCP-47 language tag")
        if self.duration_ms is not None and self.duration_ms < 1:
            raise ValueError("duration_ms must be positive when supplied")


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    segment_id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker_id: str | None = None
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptSpeaker:
    speaker_id: str
    label: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptProvenance:
    provider: str
    model: str
    audio_sha256: str
    generated_at: datetime
    model_version: str | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    transcript_id: str
    asset_id: str
    language: str
    text: str
    segments: tuple[TranscriptSegment, ...]
    speakers: tuple[TranscriptSpeaker, ...]
    provenance: TranscriptProvenance
    created_at: datetime
    schema_version: str = "1.0.0"


class TranscriptValidationError(ValueError):
    """A provider result violates the canonical transcript contract."""


def _require_uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise TranscriptValidationError(f"{field} must be a UUID") from None


def _require_confidence(value: float | None, field: str) -> None:
    if value is not None and (
        not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
    ):
        raise TranscriptValidationError(f"{field} must be between 0 and 1")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TranscriptValidationError(f"{field} must include a timezone")


def validate_transcript(
    transcript: Transcript,
    *,
    expected_asset_id: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Validate cross-field invariants that JSON Schema cannot express."""
    if transcript.schema_version != "1.0.0":
        raise TranscriptValidationError("unsupported transcript schema_version")
    _require_uuid(transcript.transcript_id, "transcript_id")
    _require_uuid(transcript.asset_id, "asset_id")
    if expected_asset_id is not None and UUID(transcript.asset_id) != UUID(expected_asset_id):
        raise TranscriptValidationError("transcript asset_id does not match the requested asset")
    if not _LANGUAGE_TAG.fullmatch(transcript.language):
        raise TranscriptValidationError("language must be a BCP-47 language tag")
    if not isinstance(transcript.text, str):
        raise TranscriptValidationError("transcript text must be a string")
    if duration_ms is not None and duration_ms < 1:
        raise TranscriptValidationError("duration_ms must be positive")

    provenance = transcript.provenance
    if not provenance.provider or not provenance.model:
        raise TranscriptValidationError("provenance provider and model are required")
    if not _SHA256.fullmatch(provenance.audio_sha256):
        raise TranscriptValidationError("provenance audio_sha256 must be a lowercase SHA-256 digest")
    _require_aware(provenance.generated_at, "provenance.generated_at")
    _require_aware(transcript.created_at, "created_at")

    speaker_ids: set[str] = set()
    for speaker in transcript.speakers:
        _require_uuid(speaker.speaker_id, "speaker_id")
        if speaker.speaker_id in speaker_ids:
            raise TranscriptValidationError("speaker_id values must be unique")
        speaker_ids.add(speaker.speaker_id)
        _require_confidence(speaker.confidence, "speaker confidence")

    segment_ids: set[str] = set()
    previous_start = -1
    for segment in transcript.segments:
        _require_uuid(segment.segment_id, "segment_id")
        if segment.segment_id in segment_ids:
            raise TranscriptValidationError("segment_id values must be unique")
        segment_ids.add(segment.segment_id)
        if segment.start_ms < 0 or segment.end_ms <= segment.start_ms:
            raise TranscriptValidationError("segment time range is invalid")
        if segment.start_ms < previous_start:
            raise TranscriptValidationError("segments must be ordered by start_ms")
        if duration_ms is not None and segment.end_ms > duration_ms:
            raise TranscriptValidationError("segment exceeds source audio duration")
        if not segment.text.strip():
            raise TranscriptValidationError("segment text cannot be empty")
        previous_start = segment.start_ms
        _require_confidence(segment.confidence, "segment confidence")
        if segment.speaker_id is not None:
            _require_uuid(segment.speaker_id, "segment speaker_id")
            if segment.speaker_id not in speaker_ids:
                raise TranscriptValidationError("segment references an unknown speaker")

        previous_word_start = segment.start_ms
        for word in segment.words:
            if word.start_ms < segment.start_ms or word.end_ms > segment.end_ms:
                raise TranscriptValidationError("word time range must stay within its segment")
            if word.end_ms <= word.start_ms or word.start_ms < previous_word_start:
                raise TranscriptValidationError("word time ranges must be valid and ordered")
            if not word.text.strip():
                raise TranscriptValidationError("word text cannot be empty")
            previous_word_start = word.start_ms
            _require_confidence(word.confidence, "word confidence")
