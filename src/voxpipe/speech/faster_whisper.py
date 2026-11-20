"""Offline, local-only faster-whisper speech-to-text adapter."""
from __future__ import annotations

import importlib
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxpipe.speech.contracts import (
    Transcript, TranscriptProvenance, TranscriptSegment, TranscriptValidationError,
    TranscriptWord, TranscriptionError, TranscriptionErrorCode, TranscriptionRequest,
    validate_transcript,
)


class FasterWhisperAdapter:
    """Local CTranslate2 adapter; model is injectable for offline tests."""
    provider_name = "faster-whisper"

    def __init__(
        self, model_name: str = "small", *, device: str = "cpu",
        compute_type: str = "int8", download_root: str | Path | None = None,
        local_files_only: bool = True, max_audio_bytes: int = 512 * 1024 * 1024,
        beam_size: int = 5, model: Any | None = None,
    ) -> None:
        if not model_name or len(model_name) > 512:
            raise ValueError("model_name must be non-empty and bounded")
        if max_audio_bytes < 1 or not 1 <= beam_size <= 10:
            raise ValueError("invalid audio or beam-size limit")
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.download_root = str(download_root) if download_root is not None else None
        self.local_files_only = local_files_only
        self.max_audio_bytes = max_audio_bytes
        self.beam_size = beam_size
        self._model = model

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                module = importlib.import_module("faster_whisper")
                self._model = module.WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type,
                    download_root=self.download_root, local_files_only=self.local_files_only,
                )
            except Exception:
                raise TranscriptionError(
                    TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                    provider=self.provider_name, retryable=False,
                ) from None
        return self._model

    @staticmethod
    def _language_code(language: str | None) -> str | None:
        return language.split("-", 1)[0].lower() if language else None

    def _stage_audio(self, request: TranscriptionRequest) -> Path:
        suffixes = {
            "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
            "audio/flac": ".flac", "audio/ogg": ".ogg", "audio/opus": ".opus",
            "audio/webm": ".webm",
        }
        mime = request.content_type.lower().split(";", 1)[0].strip()
        fd, name = tempfile.mkstemp(prefix="voxpipe-stt-", suffix=suffixes.get(mime, ".audio"))
        path = Path(name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as target:
                while True:
                    chunk = request.audio_stream.read(64 * 1024)
                    if not isinstance(chunk, bytes):
                        raise ValueError("audio stream must return bytes")
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_audio_bytes:
                        raise ValueError("audio exceeds configured adapter limit")
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if not written:
                raise ValueError("audio stream is empty")
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        if request.diarization:
            raise TranscriptionError(
                TranscriptionErrorCode.FEATURE_UNSUPPORTED,
                provider=self.provider_name, retryable=False,
            )
        try:
            audio_path = self._stage_audio(request)
        except Exception:
            raise TranscriptionError(
                TranscriptionErrorCode.INVALID_AUDIO,
                provider=self.provider_name, retryable=False,
            ) from None
        try:
            model = self._get_model()
            provider_iter, info = model.transcribe(
                str(audio_path), language=self._language_code(request.language_hint),
                beam_size=self.beam_size, word_timestamps=request.word_timestamps,
                vad_filter=False,
            )
            provider_segments = list(provider_iter)
            language = getattr(info, "language", None) or self._language_code(request.language_hint)
            if not language:
                raise ValueError("model did not return a language")
            now = datetime.now(timezone.utc)
            segments: list[TranscriptSegment] = []
            text_parts: list[str] = []
            for item in provider_segments:
                value = str(getattr(item, "text", "")).strip()
                if not value:
                    continue
                start_ms, end_ms = round(float(item.start) * 1000), round(float(item.end) * 1000)
                if start_ms < 0 or end_ms <= start_ms:
                    raise ValueError("invalid segment timestamps")
                words: list[TranscriptWord] = []
                if request.word_timestamps:
                    for word in getattr(item, "words", None) or ():
                        word_text = str(getattr(word, "word", "")).strip()
                        if not word_text:
                            continue
                        word_start = round(float(word.start) * 1000)
                        word_end = round(float(word.end) * 1000)
                        if word_start < start_ms or word_end > end_ms or word_end <= word_start:
                            raise ValueError("invalid word timestamps")
                        words.append(TranscriptWord(word_start, word_end, word_text))
                segments.append(TranscriptSegment(
                    segment_id=str(uuid.uuid4()), start_ms=start_ms, end_ms=end_ms,
                    text=value, words=tuple(words),
                ))
                text_parts.append(value)
            transcript = Transcript(
                transcript_id=str(uuid.uuid4()), asset_id=request.asset_id,
                language=language.lower(), text=" ".join(text_parts),
                segments=tuple(segments), speakers=(),
                provenance=TranscriptProvenance(
                    provider=self.provider_name, model=self.model_name,
                    audio_sha256=request.audio_sha256, generated_at=now,
                ),
                created_at=now,
            )
            validate_transcript(
                transcript, expected_asset_id=request.asset_id,
                duration_ms=request.duration_ms, expected_audio_sha256=request.audio_sha256,
            )
            return transcript
        except TranscriptionError:
            raise
        except TranscriptValidationError:
            raise TranscriptionError(
                TranscriptionErrorCode.RESPONSE_INVALID,
                provider=self.provider_name, retryable=False,
            ) from None
        except Exception:
            raise TranscriptionError(
                TranscriptionErrorCode.INVALID_AUDIO,
                provider=self.provider_name, retryable=False,
            ) from None
        finally:
            audio_path.unlink(missing_ok=True)
