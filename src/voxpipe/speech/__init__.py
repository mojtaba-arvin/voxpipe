"""Provider-neutral speech intelligence contracts and runtime primitives."""

from voxpipe.speech.capabilities import ProviderCapability, ProviderCapabilityRegistry
from voxpipe.speech.contracts import (
    Transcript,
    TranscriptProvenance,
    TranscriptSegment,
    TranscriptSpeaker,
    TranscriptValidationError,
    TranscriptWord,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    validate_transcript,
)
from voxpipe.speech.faster_whisper import FasterWhisperAdapter
from voxpipe.speech.observability import (
    NoopSpeechObserver,
    SpeechObserver,
    SpeechOperationEvent,
)
from voxpipe.speech.ports import SpeechToTextPort
from voxpipe.speech.resilience import (
    CircuitBreakerSnapshot,
    CircuitBreakingSpeechToTextPort,
    CircuitState,
)
from voxpipe.speech.service import SpeechToTextService

__all__ = [
    "CircuitBreakerSnapshot",
    "CircuitBreakingSpeechToTextPort",
    "CircuitState",
    "FasterWhisperAdapter",
    "NoopSpeechObserver",
    "ProviderCapability",
    "ProviderCapabilityRegistry",
    "SpeechObserver",
    "SpeechOperationEvent",
    "SpeechToTextPort",
    "SpeechToTextService",
    "Transcript",
    "TranscriptProvenance",
    "TranscriptSegment",
    "TranscriptSpeaker",
    "TranscriptValidationError",
    "TranscriptWord",
    "TranscriptionError",
    "TranscriptionErrorCode",
    "TranscriptionRequest",
    "validate_transcript",
]
