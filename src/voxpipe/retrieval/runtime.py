"""Configured local hybrid retrieval and idempotent transcript indexing."""
from __future__ import annotations

from threading import RLock

from voxpipe.ingestion.postgres import PostgresIngestionRepository
from voxpipe.retrieval import (
    HybridRetrievalService,
    LocalSentenceTransformerEmbeddingAdapter,
    PostgresSemanticSearchAdapter,
    RetrievalFilters,
    SearchCandidate,
)


class RetrievalIndexNotReady(RuntimeError):
    """The tenant has no segment index for the configured embedding model version."""


class LocalHybridRetrievalRuntime:
    def __init__(
        self, *, database_dsn: str, model_path: str, model: str, model_version: str, dimensions: int,
        query_prefix: str = "", document_prefix: str = "",
    ) -> None:
        if dimensions != 384:
            raise ValueError("the transcript embedding index contract requires exactly 384 dimensions")
        self.model = model
        self._model_lock = RLock()
        self.model_version = model_version
        self.repository = PostgresIngestionRepository(database_dsn)
        self.embeddings = LocalSentenceTransformerEmbeddingAdapter(
            model_path, model=model, model_version=model_version, dimensions=dimensions,
            device="cpu", query_prefix=query_prefix, document_prefix=document_prefix,
        )
        self.semantic = PostgresSemanticSearchAdapter(
            database_dsn, model=model, model_version=model_version,
        )
        self.hybrid = HybridRetrievalService(
            lexical=self.repository, embeddings=self.embeddings, semantic=self.semantic,
        )

    def index_transcript(
        self, tenant_id: str, transcript_id: str, revision: int | None = None,
    ) -> dict:
        transcript = self.repository.get_transcript_revision(tenant_id, transcript_id, revision)
        if transcript is None:
            raise LookupError("transcript revision was not found")
        selected_revision = int(transcript["revision"])
        asset_id = str(transcript["asset_id"])
        language = str(transcript["language"])
        segments = transcript.get("segments") or []
        if len(segments) > 5000:
            raise ValueError("transcript revision exceeds the 5000-segment indexing limit")
        indexed = 0
        with self._model_lock:
            for offset in range(0, len(segments), 64):
                batch = segments[offset:offset + 64]
                vectors = self.embeddings.embed_documents([segment["text"] for segment in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError("local embedding model returned an incomplete batch")
                for segment, embedding in zip(batch, vectors):
                    candidate = SearchCandidate(
                        transcript_id=transcript_id,
                        revision=selected_revision,
                        asset_id=asset_id,
                        segment_id=str(segment["segment_id"]),
                        start_ms=int(segment["start_ms"]),
                        end_ms=int(segment["end_ms"]),
                        text=segment["text"],
                        language=language,
                        speaker_id=str(segment["speaker_id"]) if segment.get("speaker_id") else None,
                    )
                    self.semantic.store_embedding(tenant_id, candidate, embedding)
                    indexed += 1
        return {
            "transcript_id": transcript_id,
            "revision": selected_revision,
            "indexed_segments": indexed,
            "model": self.model,
            "model_version": self.model_version,
        }

    def search(self, tenant_id: str, request: dict) -> dict:
        filters = RetrievalFilters(
            tenant_id=tenant_id,
            transcript_id=request.get("transcript_id"),
            asset_id=request.get("asset_id"),
            revision=request.get("revision"),
            language=request.get("language"),
            speaker_id=request.get("speaker_id"),
            start_ms=request.get("start_ms"),
            end_ms=request.get("end_ms"),
        )
        if not self.semantic.has_indexed_segments(tenant_id):
            raise RetrievalIndexNotReady("no transcript segments are indexed for the configured model version")
        with self._model_lock:
            hits = self.hybrid.search(
                request["query"], filters=filters, limit=request.get("limit", 20),
            )
        return {
            "model": self.model,
            "model_version": self.model_version,
            "items": [
                {
                    "transcript_id": hit.candidate.transcript_id,
                    "revision": hit.candidate.revision,
                    "asset_id": hit.candidate.asset_id,
                    "segment_id": hit.candidate.segment_id,
                    "start_ms": hit.candidate.start_ms,
                    "end_ms": hit.candidate.end_ms,
                    "text": hit.candidate.text,
                    "language": hit.candidate.language,
                    "speaker_id": hit.candidate.speaker_id,
                    "fused_score": hit.fused_score,
                    "lexical_rank": hit.lexical_rank,
                    "semantic_rank": hit.semantic_rank,
                }
                for hit in hits
            ],
        }
