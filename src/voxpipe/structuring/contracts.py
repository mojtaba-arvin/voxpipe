"""Typed structured-intelligence contracts with exact transcript evidence binding."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID


CLAIM_COLLECTIONS = ("entities", "events", "topics", "relations", "action_items")
@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    segment_id: str
    start_char: int
    end_char: int
    quote: str


@dataclass(frozen=True, slots=True)
class StructuredClaim:
    claim_id: str
    value: Mapping[str, object]
    evidence: tuple[EvidenceSpan, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class ExtractionProvenance:
    provider: str
    model: str
    model_version: str
    prompt_version: str
    schema_version: str
    extracted_at: datetime


@dataclass(frozen=True, slots=True)
class StructuredIntelligence:
    document_id: str
    asset_id: str
    transcript_id: str
    transcript_revision: int
    provenance: ExtractionProvenance
    entities: tuple[StructuredClaim, ...] = ()
    events: tuple[StructuredClaim, ...] = ()
    topics: tuple[StructuredClaim, ...] = ()
    relations: tuple[StructuredClaim, ...] = ()
    action_items: tuple[StructuredClaim, ...] = ()


class StructuredIntelligenceValidationError(ValueError):
    """Structured output is malformed, unsupported, or not grounded in its source."""


def _uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise StructuredIntelligenceValidationError(f"{field} must be a UUID") from None


def _version(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise StructuredIntelligenceValidationError(f"{field} must be a non-empty bounded identifier")


def validate_structured_intelligence(
    result: StructuredIntelligence,
    *,
    expected_asset_id: str,
    expected_transcript_id: str,
    expected_revision: int,
    transcript_segments: Mapping[str, str],
) -> None:
    """Validate exact evidence against an immutable transcript revision.

    transcript_segments must be loaded under the caller's authenticated tenant scope.
    This pure function deliberately accepts no tenant identifier and performs no I/O.
    """
    for field in ("document_id", "asset_id", "transcript_id"):
        _uuid(getattr(result, field), field)
    if result.asset_id != expected_asset_id or result.transcript_id != expected_transcript_id:
        raise StructuredIntelligenceValidationError("source asset/transcript binding mismatch")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise StructuredIntelligenceValidationError("expected transcript revision must be positive")
    if result.transcript_revision != expected_revision:
        raise StructuredIntelligenceValidationError("transcript revision binding mismatch")
    provenance = result.provenance
    for field in ("provider", "model", "model_version", "prompt_version", "schema_version"):
        _version(getattr(provenance, field), f"provenance.{field}")
    if provenance.schema_version != "1.0.0":
        raise StructuredIntelligenceValidationError("unsupported structured-intelligence schema version")
    if not isinstance(provenance.extracted_at, datetime) or provenance.extracted_at.tzinfo is None or provenance.extracted_at.utcoffset() is None:
        raise StructuredIntelligenceValidationError("provenance.extracted_at must be timezone-aware")

    seen_claim_ids: set[str] = set()
    for collection_name in CLAIM_COLLECTIONS:
        for claim in getattr(result, collection_name):
            _uuid(claim.claim_id, "claim_id")
            if claim.claim_id in seen_claim_ids:
                raise StructuredIntelligenceValidationError("claim IDs must be unique across collections")
            seen_claim_ids.add(claim.claim_id)
            if not isinstance(claim.value, Mapping) or not claim.value:
                raise StructuredIntelligenceValidationError("claim value must be a non-empty object")
            if not isinstance(claim.confidence, (int, float)) or isinstance(claim.confidence, bool) or not math.isfinite(claim.confidence) or not 0 <= claim.confidence <= 1:
                raise StructuredIntelligenceValidationError("claim confidence must be between 0 and 1")
            if not claim.evidence:
                raise StructuredIntelligenceValidationError("every claim requires at least one evidence span")
            for evidence in claim.evidence:
                _uuid(evidence.segment_id, "evidence.segment_id")
                source_text = transcript_segments.get(evidence.segment_id)
                if source_text is None:
                    raise StructuredIntelligenceValidationError("evidence references a segment outside the source transcript")
                if (not isinstance(evidence.start_char, int) or isinstance(evidence.start_char, bool)
                    or not isinstance(evidence.end_char, int) or isinstance(evidence.end_char, bool)
                    or evidence.start_char < 0 or evidence.end_char <= evidence.start_char
                    or evidence.end_char > len(source_text)):
                    raise StructuredIntelligenceValidationError("evidence offsets are outside the source segment")
                if not isinstance(evidence.quote, str) or source_text[evidence.start_char:evidence.end_char] != evidence.quote:
                    raise StructuredIntelligenceValidationError("evidence quote does not exactly match transcript offsets")
