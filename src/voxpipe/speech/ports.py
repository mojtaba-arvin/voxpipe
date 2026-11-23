"""Provider-neutral speech-to-text adapter protocol."""

from __future__ import annotations

from typing import Protocol

from voxpipe.speech.contracts import Transcript, TranscriptionRequest


class SpeechToTextPort(Protocol):
    """A replaceable adapter; provider SDK types must not cross this boundary."""

    @property
    def provider_name(self) -> str: ...

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        """Transcribe audio into the canonical transcript.\n\n        idempotency_key is stable across retries; adapters forward it only when\n        the selected provider documents idempotent submission semantics.\n        """
        ...
