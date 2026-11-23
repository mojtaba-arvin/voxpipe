"""Application boundary for validated and observable speech-to-text results."""

from voxpipe.speech.observability import (
    NoopSpeechObserver,
    SpeechObserver,
    SpeechOperationEvent,
    SpeechToTextService,
)

__all__ = [
    "NoopSpeechObserver",
    "SpeechObserver",
    "SpeechOperationEvent",
    "SpeechToTextService",
]
