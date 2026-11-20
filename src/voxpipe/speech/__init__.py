"""Provider-neutral speech intelligence contracts."""

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
