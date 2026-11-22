"""Deterministic reciprocal-rank fusion for tenant-scoped transcript retrieval."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence
from uuid import UUID

from voxpipe.retrieval.ports import EmbeddingPort, RetrievalFilters, SemanticSearchPort


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """A source-attributed transcript segment returned by a retrieval backend."""

    transcript_id: str
    revision: int
    asset_id: str
    segment_id: str
    start_ms: int
    end_ms: int
    text: str
    language: str
    speaker_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("transcript_id", "asset_id", "segment_id"):
            try:
                UUID(getattr(self, field))
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"{field} must be a UUID") from None
        if self.speaker_id is not None:
            try:
                UUID(self.speaker_id)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("speaker_id must be a UUID or null") from None
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        if (not isinstance(self.start_ms, int) or isinstance(self.start_ms, bool) or self.start_ms < 0
                or not isinstance(self.end_ms, int) or isinstance(self.end_ms, bool) or self.end_ms <= self.start_ms):
            raise ValueError("candidate timestamps are invalid")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("candidate text must be non-empty")
        if not isinstance(self.language, str) or not self.language.strip():
            raise ValueError("candidate language must be non-empty")

    @property
    def identity(self) -> tuple[str, int, str]:
        return (self.transcript_id, self.revision, self.segment_id)


@dataclass(frozen=True, slots=True)
class SearchHit:
    candidate: SearchCandidate
    fused_score: float
    lexical_rank: int | None
    semantic_rank: int | None


class LexicalSearchPort(Protocol):
    def search_transcript_segments(
        self, tenant_id: str, query: str, *, transcript_id: str | None = None,
        asset_id: str | None = None, revision: int | None = None, language: str | None = None,
        speaker_id: str | None = None, start_ms: int | None = None, end_ms: int | None = None,
        limit: int = 20, offset: int = 0,
    ) -> list[dict]: ...


def _candidate(value: SearchCandidate | dict) -> SearchCandidate:
    if isinstance(value, SearchCandidate):
        return value
    if not isinstance(value, dict):
        raise TypeError("retrieval backends must return SearchCandidate or mapping rows")
    return SearchCandidate(
        transcript_id=str(value["transcript_id"]), revision=value["revision"],
        asset_id=str(value["asset_id"]), segment_id=str(value["segment_id"]),
        start_ms=value["start_ms"], end_ms=value["end_ms"], text=value["text"],
        language=value["language"], speaker_id=str(value["speaker_id"]) if value.get("speaker_id") is not None else None,
    )


def reciprocal_rank_fusion(
    lexical: Sequence[SearchCandidate | dict], semantic: Sequence[SearchCandidate | dict], *,
    lexical_weight: float = 1.0, semantic_weight: float = 1.0, rank_constant: int = 60,
    limit: int = 20,
) -> tuple[SearchHit, ...]:
    """Fuse independently ranked result lists without trusting backend score scales.

    A duplicate identity must carry identical immutable source metadata in both
    lists; disagreement is rejected rather than silently combining provenance.
    """
    for value, name in ((lexical_weight, "lexical_weight"), (semantic_weight, "semantic_weight")):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
    if lexical_weight + semantic_weight <= 0:
        raise ValueError("at least one retrieval weight must be positive")
    if not isinstance(rank_constant, int) or isinstance(rank_constant, bool) or rank_constant < 1:
        raise ValueError("rank_constant must be a positive integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    candidates: dict[tuple[str, int, str], SearchCandidate] = {}
    ranks: dict[tuple[str, int, str], dict[str, int]] = {}
    for source, rows, weight in (("lexical", lexical, lexical_weight), ("semantic", semantic, semantic_weight)):
        seen: set[tuple[str, int, str]] = set()
        for rank, raw in enumerate(rows, start=1):
            candidate = _candidate(raw)
            key = candidate.identity
            if key in seen:
                continue
            seen.add(key)
            existing = candidates.get(key)
            if existing is not None and existing != candidate:
                raise ValueError("retrieval backends disagree on immutable candidate provenance")
            candidates[key] = candidate
            ranks.setdefault(key, {})[source] = rank

    hits = []
    for key, candidate in candidates.items():
        lexical_rank = ranks[key].get("lexical")
        semantic_rank = ranks[key].get("semantic")
        score = 0.0
        if lexical_rank is not None:
            score += lexical_weight / (rank_constant + lexical_rank)
        if semantic_rank is not None:
            score += semantic_weight / (rank_constant + semantic_rank)
        hits.append(SearchHit(candidate, score, lexical_rank, semantic_rank))
    hits.sort(key=lambda hit: (
        -hit.fused_score, hit.candidate.transcript_id, hit.candidate.revision,
        hit.candidate.start_ms, hit.candidate.segment_id,
    ))
    return tuple(hits[:limit])


class HybridRetrievalService:
    """Coordinate lexical/vector backends and preserve authenticated source filters."""

    def __init__(self, *, lexical: LexicalSearchPort, embeddings: EmbeddingPort,
                 semantic: SemanticSearchPort, lexical_weight: float = 1.0,
                 semantic_weight: float = 1.0, rank_constant: int = 60) -> None:
        self._lexical, self._embeddings, self._semantic = lexical, embeddings, semantic
        self._lexical_weight, self._semantic_weight, self._rank_constant = lexical_weight, semantic_weight, rank_constant
        # Validate policy at construction, before any request is served.
        reciprocal_rank_fusion((), (), lexical_weight=lexical_weight,
            semantic_weight=semantic_weight, rank_constant=rank_constant, limit=1)

    def search(self, query: str, *, filters: RetrievalFilters, limit: int = 20) -> tuple[SearchHit, ...]:
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            raise ValueError("query must contain 1..256 characters")
        if not isinstance(filters, RetrievalFilters):
            raise TypeError("filters must include authenticated tenant scope")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        lexical = self._lexical.search_transcript_segments(
            filters.tenant_id, query, transcript_id=filters.transcript_id,
            asset_id=filters.asset_id, revision=filters.revision, language=filters.language,
            speaker_id=filters.speaker_id, start_ms=filters.start_ms, end_ms=filters.end_ms,
            limit=limit, offset=0,
        )
        embedding = self._embeddings.embed_query(query)
        semantic = self._semantic.search_similar(
            filters.tenant_id, embedding, filters=filters, limit=limit,
        )
        return reciprocal_rank_fusion(lexical, semantic,
            lexical_weight=self._lexical_weight, semantic_weight=self._semantic_weight,
            rank_constant=self._rank_constant, limit=limit)
