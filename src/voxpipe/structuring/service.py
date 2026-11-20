"""Application boundary for evidence-grounded structured extraction."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from voxpipe.structuring.contracts import (
    StructuredIntelligence,
    StructuredIntelligenceValidationError,
    validate_structured_intelligence,
)


@dataclass(frozen=True, slots=True)
class StructuredExtractionRequest:
    document_id: str
    asset_id: str
    transcript_id: str
    transcript_revision: int
    language: str
    transcript_segments: Mapping[str, str]

    def __post_init__(self) -> None:
        for field in ("document_id", "asset_id", "transcript_id"):
            try:
                UUID(getattr(self, field))
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"{field} must be a UUID") from None
        if (not isinstance(self.transcript_revision, int)
            or isinstance(self.transcript_revision, bool)
            or self.transcript_revision < 1):
            raise ValueError("transcript_revision must be a positive integer")
        if not isinstance(self.language, str) or not self.language.strip() or len(self.language) > 35:
            raise ValueError("language must be a non-empty bounded tag")
        if not isinstance(self.transcript_segments, Mapping) or not self.transcript_segments:
            raise ValueError("transcript_segments must be a non-empty mapping")
        snapshot: dict[str, str] = {}
        for segment_id, text in self.transcript_segments.items():
            try:
                normalized_id = str(UUID(segment_id))
            except (ValueError, TypeError, AttributeError):
                raise ValueError("transcript segment keys must be UUIDs") from None
            if not isinstance(text, str):
                raise ValueError("transcript segment text must be a string")
            snapshot[normalized_id] = text
        object.__setattr__(self, "transcript_segments", MappingProxyType(snapshot))


class StructuredExtractionService:
    """Run an injected extractor and reject unsupported or misbound claims.

    The request must be constructed from a transcript revision already loaded
    under the caller's authenticated tenant scope. Provider output is never
    trusted: every evidence span is checked against the immutable source text.
    """

    def __init__(self, adapter) -> None:
        provider_name = getattr(adapter, "provider_name", None)
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise ValueError("structured extraction adapter must expose provider_name")
        self._adapter = adapter
        self._provider_name = provider_name

    def extract(self, request: StructuredExtractionRequest) -> StructuredIntelligence:
        result = self._adapter.extract(request)
        if result.provenance.provider != self._provider_name:
            raise StructuredIntelligenceValidationError(
                "extraction provenance does not match the selected provider"
            )
        validate_structured_intelligence(
            result,
            expected_asset_id=request.asset_id,
            expected_transcript_id=request.transcript_id,
            expected_revision=request.transcript_revision,
            transcript_segments=request.transcript_segments,
        )
        if result.document_id != request.document_id:
            raise StructuredIntelligenceValidationError("document binding mismatch")
        return result
