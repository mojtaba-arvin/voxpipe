-- Versioned 384-dimensional semantic index for immutable transcript segments.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE transcript_segment_embeddings (
    tenant_id UUID NOT NULL,
    transcript_id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    segment_id UUID NOT NULL,
    embedding_model TEXT NOT NULL CHECK (length(embedding_model) BETWEEN 1 AND 128),
    embedding_version TEXT NOT NULL CHECK (length(embedding_version) BETWEEN 1 AND 128),
    embedding vector(384) NOT NULL,
    source_text_sha256 CHAR(64) NOT NULL CHECK (source_text_sha256 ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, transcript_id, revision, segment_id, embedding_model, embedding_version),
    CONSTRAINT fk_embedding_transcript_segment
        FOREIGN KEY (tenant_id, transcript_id, revision, segment_id)
        REFERENCES transcript_segments (tenant_id, transcript_id, revision, segment_id) ON DELETE CASCADE
);
CREATE INDEX idx_transcript_embeddings_hnsw
    ON transcript_segment_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_transcript_embeddings_tenant_model
    ON transcript_segment_embeddings (tenant_id, embedding_model, embedding_version);
ALTER TABLE transcript_segment_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_segment_embeddings FORCE ROW LEVEL SECURITY;
CREATE POLICY transcript_segment_embeddings_tenant_isolation ON transcript_segment_embeddings
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);
