"""Application boundary for validated speech-to-text results."""

from __future__ import annotations

from voxpipe.speech.contracts import Transcript, TranscriptionRequest, validate_transcript
from voxpipe.speech.ports import SpeechToTextPort


class SpeechToTextService:
    def __init__(self, adapter: SpeechToTextPort) -> None:
        self._adapter = adapter

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        result = self._adapter.transcribe(request)
        validate_transcript(
            result,
            expected_asset_id=request.asset_id,
            duration_ms=request.duration_ms,
        )
        return result
