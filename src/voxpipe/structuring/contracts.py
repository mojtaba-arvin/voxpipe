"""Typed structured-intelligence contracts with exact transcript evidence binding."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID


CLAIM_COLLECTIONS = ("entities", "events", "topics", "relations", "action_items")
CLAIM_VALUE_REQUIRED_FIELDS = {
    "entities": ("name", "entity_type"), "events": ("name", "event_type"),
    "topics": ("name",), "relations": ("subject", "predicate", "object"),
    "action_items": ("description",),
}
@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    segment_id: str
    start_char: int
    end_char: int
    quote: str
    word_start_index: int | None = None
    word_end_index: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TranscriptWordEvidence:
    word_index: int
    start_ms: int
    end_ms: int
    text: str


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
    transcript_words: Mapping[str, tuple[TranscriptWordEvidence, ...]] | None = None,
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
            for field in CLAIM_VALUE_REQUIRED_FIELDS[collection_name]:
                value = claim.value.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise StructuredIntelligenceValidationError(f"{collection_name} claim requires non-empty string field {field}")
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
                word_fields = (evidence.word_start_index, evidence.word_end_index, evidence.start_ms, evidence.end_ms)
                if any(value is not None for value in word_fields):
                    if any(value is None for value in word_fields):
                        raise StructuredIntelligenceValidationError("word evidence requires word indices and timestamps")
                    if (not isinstance(evidence.word_start_index,int) or isinstance(evidence.word_start_index,bool)
                        or not isinstance(evidence.word_end_index,int) or isinstance(evidence.word_end_index,bool)
                        or evidence.word_start_index < 0 or evidence.word_end_index <= evidence.word_start_index
                        or not isinstance(evidence.start_ms,int) or isinstance(evidence.start_ms,bool)
                        or not isinstance(evidence.end_ms,int) or isinstance(evidence.end_ms,bool)
                        or evidence.start_ms < 0 or evidence.end_ms <= evidence.start_ms):
                        raise StructuredIntelligenceValidationError("word evidence indices/timestamps are invalid")
                    source_words=(transcript_words or {}).get(evidence.segment_id,())
                    selected=[word for word in source_words if evidence.word_start_index <= word.word_index < evidence.word_end_index]
                    count=evidence.word_end_index-evidence.word_start_index
                    if len(selected)!=count or not selected or selected[0].word_index!=evidence.word_start_index or selected[-1].word_index!=evidence.word_end_index-1:
                        raise StructuredIntelligenceValidationError("word evidence is outside the immutable transcript")
                    if evidence.start_ms!=selected[0].start_ms or evidence.end_ms!=selected[-1].end_ms:
                        raise StructuredIntelligenceValidationError("word evidence timestamps do not match source word timings")
                    cursor=0
                    for word in selected:
                        position=evidence.quote.find(word.text,cursor)
                        if position<0: raise StructuredIntelligenceValidationError("word text is not present in cited quote")
                        cursor=position+len(word.text)
