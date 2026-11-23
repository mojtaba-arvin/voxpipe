"""Provider-neutral speech intelligence contracts."""

from voxpipe.speech.capabilities import ProviderCapability, ProviderCapabilityRegistry
from voxpipe.speech.circuit_breaker import ProviderCircuitBreaker
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
from voxpipe.speech.ports import SpeechToTextPort
from voxpipe.speech.service import SpeechToTextService

__all__ = [
    "FasterWhisperAdapter",
    "ProviderCapability",
    "ProviderCapabilityRegistry",
    "ProviderCircuitBreaker",
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
