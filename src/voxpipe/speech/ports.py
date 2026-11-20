"""Provider-neutral speech-to-text adapter protocol."""

from __future__ import annotations

from typing import Protocol

from voxpipe.speech.contracts import Transcript, TranscriptionRequest


class SpeechToTextPort(Protocol):
    """A replaceable adapter; provider SDK types must not cross this boundary."""

    @property
    def provider_name(self) -> str: ...

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        """Transcribe an extracted audio stream into the canonical transcript."""
        ...
