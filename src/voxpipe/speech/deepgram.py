"""Hosted Deepgram Nova-3 batch adapter; network access is explicit and injectable."""
from __future__ import annotations

import json
import math
import os
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from voxpipe.speech.contracts import (
    Transcript,
    TranscriptProvenance,
    TranscriptSegment,
    TranscriptSpeaker,
    TranscriptWord,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    validate_transcript,
)


class DeepgramNova3Adapter:
    """Deepgram Nova-3 multilingual batch STT behind the canonical speech port.

    The adapter never sends provider-specific objects outside this boundary.
    It deliberately does not forward the workflow idempotency key: the batch
    listen endpoint does not document request idempotency, so ambiguous network
    failures are marked non-retryable to avoid duplicate paid inference.
    """

    provider_name = "deepgram"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "nova-3",
        timeout_seconds: float = 60.0,
        max_audio_bytes: int = 512 * 1024 * 1024,
        max_response_bytes: int = 16 * 1024 * 1024,
        endpoint: str = "https://api.deepgram.com/v1/listen",
        http_opener: Callable[..., Any] = urlopen,
    ) -> None:
        if model != "nova-3":
            raise ValueError("only the explicitly evaluated nova-3 model is supported")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if max_audio_bytes < 1 or max_response_bytes < 1:
            raise ValueError("audio and response limits must be positive")
        if not endpoint.startswith("https://"):
            raise ValueError("Deepgram endpoint must use HTTPS")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_audio_bytes = max_audio_bytes
        self.max_response_bytes = max_response_bytes
        self.endpoint = endpoint
        self._api_key = api_key if api_key is not None else os.getenv("DEEPGRAM_API_KEY")
        self._http_opener = http_opener

    @staticmethod
    def _seconds_to_ms(value: Any) -> int:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError("provider timestamp is not finite")
        return round(value * 1000)

    @staticmethod
    def _speaker_uuid(asset_id: str, provider_speaker: Any) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"voxpipe:deepgram:{asset_id}:{provider_speaker}"))

    def _read_audio(self, request: TranscriptionRequest) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = request.audio_stream.read(min(64 * 1024, self.max_audio_bytes + 1 - total))
            if not isinstance(chunk, bytes):
                raise ValueError("audio stream must return bytes")
            if not chunk:
                break
            total += len(chunk)
            if total > self.max_audio_bytes:
                raise ValueError("audio exceeds configured adapter limit")
            chunks.append(chunk)
        if total == 0:
            raise ValueError("audio stream is empty")
        return b"".join(chunks)

    def _request(self, request: TranscriptionRequest, audio: bytes) -> dict[str, Any]:
        if not self._api_key:
            raise TranscriptionError(
                TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                provider=self.provider_name,
                retryable=False,
            )
        params: list[tuple[str, str]] = [
            ("model", self.model),
            ("language", "multi"),
            ("utterances", "true"),
        ]
        if request.language_hint:
            params.append(("language_hint", request.language_hint.split("-", 1)[0].lower()))
        if request.diarization:
            params.append(("diarize_model", "latest"))
        url = f"{self.endpoint}?{urlencode(params)}"
        outgoing = Request(
            url,
            data=audio,
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": request.content_type.split(";", 1)[0].strip().lower(),
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._http_opener(outgoing, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise TranscriptionError(
                        TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                        provider=self.provider_name,
                        retryable=status >= 500,
                    )
                payload = response.read(self.max_response_bytes + 1)
            if len(payload) > self.max_response_bytes:
                raise ValueError("provider response exceeds configured limit")
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("provider response must be an object")
            return decoded
        except HTTPError as error:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            retry_seconds = None
            if retry_after and retry_after.isdigit():
                retry_seconds = min(int(retry_after), 3600)
            if error.code in (401, 403):
                code, retryable = TranscriptionErrorCode.AUTHENTICATION_FAILED, False
            elif error.code == 429:
                code, retryable = TranscriptionErrorCode.RATE_LIMITED, True
            elif error.code in (400, 413, 415, 422):
                code, retryable = TranscriptionErrorCode.INVALID_AUDIO, False
            else:
                code, retryable = TranscriptionErrorCode.PROVIDER_UNAVAILABLE, error.code >= 500
            raise TranscriptionError(
                code, provider=self.provider_name, retryable=retryable,
                retry_after_seconds=retry_seconds if code == TranscriptionErrorCode.RATE_LIMITED else None,
            ) from None
        except (TimeoutError, socket.timeout, URLError, OSError):
            # The server may have accepted and billed the request before the
            # connection failed. Do not let the durable worker retry blindly.
            raise TranscriptionError(
                TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                provider=self.provider_name,
                retryable=False,
            ) from None
        except TranscriptionError:
            raise
        except Exception:
            raise TranscriptionError(
                TranscriptionErrorCode.RESPONSE_INVALID,
                provider=self.provider_name,
                retryable=False,
            ) from None

    def _to_transcript(self, request: TranscriptionRequest, payload: dict[str, Any]) -> Transcript:
        try:
            results = payload["results"]
            channels = results["channels"]
            alternative = channels[0]["alternatives"][0]
            transcript_text = alternative.get("transcript", "")
            if not isinstance(transcript_text, str):
                raise ValueError("transcript must be text")
            detected = alternative.get("languages") or []
            language = detected[0] if detected else (
                request.language_hint.split("-", 1)[0].lower() if request.language_hint else "und"
            )
            if not isinstance(language, str) or not language:
                raise ValueError("language is invalid")

            utterances = results.get("utterances") or []
            raw_segments: list[dict[str, Any]] = []
            if utterances:
                raw_segments = utterances
            elif alternative.get("words"):
                # Fallback for provider responses without utterance grouping:
                # group contiguous words by speaker, preserving source order.
                for word in alternative["words"]:
                    speaker = word.get("speaker")
                    if raw_segments and raw_segments[-1].get("speaker") == speaker:
                        raw_segments[-1]["words"].append(word)
                    else:
                        raw_segments.append({"speaker": speaker, "words": [word]})
            elif transcript_text.strip():
                raise ValueError("provider omitted timestamped words and utterances")

            now = datetime.now(timezone.utc)
            speaker_ids: dict[str, str] = {}
            speakers: list[TranscriptSpeaker] = []
            segments: list[TranscriptSegment] = []
            text_parts: list[str] = []
            for raw in raw_segments:
                raw_words = raw.get("words") or []
                word_items: list[TranscriptWord] = []
                word_texts: list[str] = []
                word_speakers: set[str] = set()
                for word in raw_words:
                    value = word.get("punctuated_word") or word.get("word") or ""
                    if not isinstance(value, str) or not value.strip():
                        continue
                    start = self._seconds_to_ms(word["start"])
                    end = self._seconds_to_ms(word["end"])
                    confidence = word.get("confidence")
                    if confidence is not None and (
                        not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                        or not math.isfinite(confidence) or not 0 <= confidence <= 1
                    ):
                        raise ValueError("word confidence is invalid")
                    if request.word_timestamps:
                        word_items.append(TranscriptWord(start, end, value, confidence))
                    word_texts.append(value)
                    if word.get("speaker") is not None:
                        word_speakers.add(str(word["speaker"]))
                segment_text = raw.get("transcript")
                if not isinstance(segment_text, str) or not segment_text.strip():
                    segment_text = " ".join(word_texts).strip()
                if not segment_text:
                    continue
                if "start" in raw and "end" in raw:
                    start_ms = self._seconds_to_ms(raw["start"])
                    end_ms = self._seconds_to_ms(raw["end"])
                elif raw_words:
                    start_ms = self._seconds_to_ms(raw_words[0]["start"])
                    end_ms = self._seconds_to_ms(raw_words[-1]["end"])
                else:
                    raise ValueError("utterance has no timestamps")
                speaker = raw.get("speaker")
                if speaker is None and len(word_speakers) == 1:
                    speaker = next(iter(word_speakers))
                speaker_id = None
                if speaker is not None:
                    key = str(speaker)
                    speaker_id = speaker_ids.setdefault(key, self._speaker_uuid(request.asset_id, key))
                    if all(item.speaker_id != speaker_id for item in speakers):
                        speakers.append(TranscriptSpeaker(speaker_id, f"speaker_{key}"))
                if request.diarization and speaker_id is None:
                    raise ValueError("diarization was requested but speaker labels are missing")
                confidence = raw.get("confidence")
                if confidence is not None and (
                    not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1
                ):
                    raise ValueError("segment confidence is invalid")
                segments.append(TranscriptSegment(
                    segment_id=str(uuid.uuid4()), start_ms=start_ms, end_ms=end_ms,
                    text=segment_text.strip(), confidence=confidence, speaker_id=speaker_id,
                    words=tuple(word_items),
                ))
                text_parts.append(segment_text.strip())

            transcript = Transcript(
                transcript_id=str(uuid.uuid4()),
                asset_id=request.asset_id,
                language=language.lower(),
                text=transcript_text.strip() or " ".join(text_parts),
                segments=tuple(segments),
                speakers=tuple(speakers),
                provenance=TranscriptProvenance(
                    provider=self.provider_name,
                    model=self.model,
                    audio_sha256=request.audio_sha256,
                    generated_at=now,
                    model_version=None,
                ),
                created_at=now,
            )
            validate_transcript(
                transcript,
                expected_asset_id=request.asset_id,
                duration_ms=request.duration_ms,
                expected_audio_sha256=request.audio_sha256,
            )
            return transcript
        except Exception:
            raise TranscriptionError(
                TranscriptionErrorCode.RESPONSE_INVALID,
                provider=self.provider_name,
                retryable=False,
            ) from None

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        if not isinstance(request, TranscriptionRequest):
            raise TypeError("request must be a TranscriptionRequest")
        if not self._api_key:
            raise TranscriptionError(
                TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                provider=self.provider_name,
                retryable=False,
            )
        try:
            audio = self._read_audio(request)
        except Exception:
            raise TranscriptionError(
                TranscriptionErrorCode.INVALID_AUDIO,
                provider=self.provider_name,
                retryable=False,
            ) from None
        payload = self._request(request, audio)
        return self._to_transcript(request, payload)
