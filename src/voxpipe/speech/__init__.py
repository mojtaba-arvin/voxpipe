"""Provider-neutral speech intelligence contracts and runtime primitives."""

from voxpipe.speech.capabilities import ProviderCapability, ProviderCapabilityRegistry
from voxpipe.speech.circuit_breaker import CircuitBreakerPort, CircuitPermit, ProviderCircuitBreaker
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
from voxpipe.speech.deepgram import DeepgramNova3Adapter
from voxpipe.speech.observability import (
    NoopSpeechObserver,
    OpenTelemetrySpeechObserver,
    SpeechObserver,
    SpeechOperationEvent,
)
from voxpipe.speech.ports import SpeechToTextPort
from voxpipe.speech.postgres_circuit_breaker import PostgresProviderCircuitBreaker
from voxpipe.speech.resilience import CircuitBreakingSpeechToTextPort
from voxpipe.speech.runtime import SpeechResilienceRuntime, build_speech_resilience_runtime
from voxpipe.speech.service import SpeechToTextService

__all__ = [
    "CircuitBreakingSpeechToTextPort",
    "CircuitBreakerPort",
    "CircuitPermit",
    "FasterWhisperAdapter",
    "DeepgramNova3Adapter",
    "NoopSpeechObserver",
    "OpenTelemetrySpeechObserver",
    "ProviderCapability",
    "ProviderCapabilityRegistry",
    "ProviderCircuitBreaker",
    "PostgresProviderCircuitBreaker",
    "SpeechObserver",
    "SpeechResilienceRuntime",
    "build_speech_resilience_runtime",
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
