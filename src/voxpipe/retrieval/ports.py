"""Provider-neutral embedding and semantic retrieval contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Embedding:
    """A versioned query embedding; provider-specific SDK objects never cross this port."""

    model: str
    model_version: str
    dimensions: int
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        for field in ("model", "model_version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if not isinstance(self.dimensions, int) or isinstance(self.dimensions, bool) or self.dimensions < 1:
            raise ValueError("dimensions must be a positive integer")
        if not isinstance(self.values, tuple) or len(self.values) != self.dimensions:
            raise ValueError("embedding values must match the declared dimensions")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) for value in self.values):
            raise ValueError("embedding values must be finite numbers")


class EmbeddingPort(Protocol):
    """Creates deterministic, version-attributed embeddings for query text."""

    def embed_query(self, text: str) -> Embedding: ...


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Authenticated tenant scope plus optional source/time/speaker/entity filters."""

    tenant_id: str
    transcript_id: str | None = None
    asset_id: str | None = None
    revision: int | None = None
    language: str | None = None
    speaker_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    entity_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must come from authenticated request context")
        for field in ("transcript_id", "asset_id", "speaker_id"):
            value = getattr(self, field)
            if value is not None:
                try:
                    UUID(value)
                except (ValueError, TypeError, AttributeError):
                    raise ValueError(f"{field} must be a UUID") from None
        if self.revision is not None and (
            not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1
        ):
            raise ValueError("revision must be a positive integer")
        if self.language is not None and (not isinstance(self.language, str) or not self.language.strip() or len(self.language) > 35):
            raise ValueError("language must be a non-empty bounded tag")
        if self.start_ms is not None and (
            not isinstance(self.start_ms, int) or isinstance(self.start_ms, bool) or self.start_ms < 0
        ):
            raise ValueError("start_ms must be a non-negative integer")
        if self.end_ms is not None and (
            not isinstance(self.end_ms, int) or isinstance(self.end_ms, bool) or self.end_ms < 1
        ):
            raise ValueError("end_ms must be a positive integer")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if not isinstance(self.entity_ids, tuple):
            raise ValueError("entity_ids must be a tuple")
        for entity_id in self.entity_ids:
            try:
                UUID(entity_id)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("entity_ids must contain UUIDs") from None


class SemanticSearchPort(Protocol):
    """Vector backend must enforce tenant RLS and every supplied filter."""

    def search_similar(
        self, tenant_id: str, embedding: Embedding, *, filters: RetrievalFilters, limit: int,
    ) -> tuple[object, ...]: ...
