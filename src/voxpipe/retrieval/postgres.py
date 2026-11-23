"""Tenant-isolated pgvector storage and semantic search adapter."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
from uuid import UUID

from voxpipe.retrieval.hybrid import SearchCandidate
from voxpipe.retrieval.ports import Embedding, RetrievalFilters


class PostgresSemanticSearchAdapter:
    """Store and query versioned 384-dim segment vectors under forced RLS."""
    DIMENSIONS = 384

    def __init__(self, dsn: str, *, model: str, model_version: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise ValueError("model must be a non-empty bounded string")
        if not isinstance(model_version, str) or not model_version.strip() or len(model_version) > 128:
            raise ValueError("model_version must be a non-empty bounded string")
        self._dsn, self.model, self.model_version = dsn, model, model_version

    @contextmanager
    def _tenant_connection(self, tenant_id: str):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_id,))
            yield connection

    @classmethod
    def _vector_literal(cls, embedding: Embedding) -> str:
        if not isinstance(embedding, Embedding) or embedding.dimensions != cls.DIMENSIONS:
            raise ValueError(f"embedding must have exactly {cls.DIMENSIONS} dimensions")
        if not any(value != 0 for value in embedding.values):
            raise ValueError("cosine similarity does not accept a zero vector")
        return "[" + ",".join(format(float(value), ".9g") for value in embedding.values) + "]"

    def store_embedding(self, tenant_id: str, candidate: SearchCandidate, embedding: Embedding) -> None:
        try:
            tenant = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id must be a UUID") from None
        if not isinstance(candidate, SearchCandidate):
            raise TypeError("candidate must be a SearchCandidate")
        if embedding.model != self.model or embedding.model_version != self.model_version:
            raise ValueError("embedding model/version does not match this index adapter")
        vector = self._vector_literal(embedding)
        source_hash = hashlib.sha256(candidate.text.encode("utf-8")).hexdigest()
        with self._tenant_connection(tenant) as connection:
            source = connection.execute(
                """SELECT s.text, t.asset_id
                   FROM transcript_segments AS s
                   JOIN transcripts AS t
                     ON t.tenant_id=s.tenant_id AND t.transcript_id=s.transcript_id AND t.revision=s.revision
                   WHERE s.tenant_id=%s AND s.transcript_id=%s AND s.revision=%s AND s.segment_id=%s""",
                (tenant, candidate.transcript_id, candidate.revision, candidate.segment_id),
            ).fetchone()
            if source is None or str(source["asset_id"]) != candidate.asset_id or source["text"] != candidate.text:
                raise ValueError("candidate does not match the immutable tenant-scoped transcript segment")
            connection.execute(
                """INSERT INTO transcript_segment_embeddings
                       (tenant_id, transcript_id, revision, segment_id, embedding_model,
                        embedding_version, embedding, source_text_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::vector,%s)
                   ON CONFLICT (tenant_id, transcript_id, revision, segment_id, embedding_model, embedding_version)
                   DO UPDATE SET embedding=EXCLUDED.embedding,
                       source_text_sha256=EXCLUDED.source_text_sha256, updated_at=now()""",
                (tenant, candidate.transcript_id, candidate.revision, candidate.segment_id,
                 self.model, self.model_version, vector, source_hash),
            )

    def search_similar(self, tenant_id: str, embedding: Embedding, *, filters: RetrievalFilters, limit: int):
        try:
            tenant = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id must be a UUID") from None
        if not isinstance(filters, RetrievalFilters):
            raise ValueError("filters must be RetrievalFilters")
        try:
            filter_tenant = str(UUID(filters.tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("retrieval filter tenant_id must be a UUID") from None
        if filter_tenant != tenant:
            raise ValueError("semantic search tenant must match authenticated retrieval filters")
        if filters.entity_ids:
            raise ValueError("entity filters require the not-yet-implemented extraction entity index")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if embedding.model != self.model or embedding.model_version != self.model_version:
            raise ValueError("query embedding model/version does not match indexed vectors")
        vector = self._vector_literal(embedding)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """SELECT s.transcript_id, s.revision, t.asset_id, s.segment_id,
                          s.start_ms, s.end_ms, s.text, t.language, s.speaker_id
                   FROM transcript_segment_embeddings AS e
                   JOIN transcript_segments AS s
                     ON s.tenant_id=e.tenant_id AND s.transcript_id=e.transcript_id
                    AND s.revision=e.revision AND s.segment_id=e.segment_id
                   JOIN transcripts AS t
                     ON t.tenant_id=s.tenant_id AND t.transcript_id=s.transcript_id
                    AND t.revision=s.revision
                   WHERE e.tenant_id=%s AND e.embedding_model=%s AND e.embedding_version=%s
                     AND (%s::uuid IS NULL OR s.transcript_id=%s::uuid)
                     AND (%s::uuid IS NULL OR t.asset_id=%s::uuid)
                     AND (%s::integer IS NULL OR s.revision=%s::integer)
                     AND (%s::text IS NULL OR t.language=%s::text)
                     AND (%s::uuid IS NULL OR s.speaker_id=%s::uuid)
                     AND (%s::bigint IS NULL OR s.end_ms>%s::bigint)
                     AND (%s::bigint IS NULL OR s.start_ms<%s::bigint)
                   ORDER BY e.embedding <=> %s::vector, s.start_ms, s.segment_id
                   LIMIT %s""",
                (tenant, self.model, self.model_version,
                 filters.transcript_id, filters.transcript_id, filters.asset_id, filters.asset_id,
                 filters.revision, filters.revision, filters.language, filters.language,
                 filters.speaker_id, filters.speaker_id, filters.start_ms, filters.start_ms,
                 filters.end_ms, filters.end_ms, vector, limit),
            ).fetchall()
        return tuple(SearchCandidate(
            transcript_id=str(row["transcript_id"]), revision=row["revision"],
            asset_id=str(row["asset_id"]), segment_id=str(row["segment_id"]),
            start_ms=row["start_ms"], end_ms=row["end_ms"], text=row["text"],
            language=row["language"], speaker_id=str(row["speaker_id"]) if row["speaker_id"] else None,
        ) for row in rows)

    def delete_model_version(self, tenant_id: str, *, model: str, model_version: str) -> int:
        try:
            tenant = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id must be a UUID") from None
        if model != self.model or model_version != self.model_version:
            raise ValueError("delete request must match this adapter's model/version")
        with self._tenant_connection(tenant) as connection:
            cursor = connection.execute(
                "DELETE FROM transcript_segment_embeddings WHERE tenant_id=%s AND embedding_model=%s AND embedding_version=%s",
                (tenant, model, model_version),
            )
            return cursor.rowcount
