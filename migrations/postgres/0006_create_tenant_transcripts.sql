-- Immutable, tenant-scoped transcript revisions and searchable segments.
-- Transcript-derived rows cascade with their source asset for retention/deletion.
CREATE TABLE transcripts (
    transcript_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    asset_id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    language TEXT NOT NULL CHECK (length(language) BETWEEN 2 AND 35),
    text TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 128),
    model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 256),
    model_version TEXT,
    audio_sha256 CHAR(64) NOT NULL CHECK (audio_sha256 ~ '^[a-f0-9]{64}$'),
    generated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, transcript_id, revision),
    CONSTRAINT uq_transcript_revision_asset UNIQUE (tenant_id, transcript_id, revision, asset_id),
    CONSTRAINT fk_transcript_tenant_asset
        FOREIGN KEY (tenant_id, asset_id)
        REFERENCES audio_assets (tenant_id, asset_id) ON DELETE CASCADE
);

CREATE INDEX idx_transcripts_tenant_asset
    ON transcripts (tenant_id, asset_id, created_at DESC);
CREATE INDEX idx_transcripts_tenant_language
    ON transcripts (tenant_id, language, created_at DESC);

CREATE TABLE transcript_speakers (
    tenant_id UUID NOT NULL,
    transcript_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    speaker_id UUID NOT NULL,
    label TEXT,
    confidence DOUBLE PRECISION CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    PRIMARY KEY (tenant_id, transcript_id, revision, speaker_id),
    CONSTRAINT fk_speaker_transcript
        FOREIGN KEY (tenant_id, transcript_id, revision)
        REFERENCES transcripts (tenant_id, transcript_id, revision) ON DELETE CASCADE
);

CREATE TABLE transcript_segments (
    tenant_id UUID NOT NULL,
    transcript_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    segment_id UUID NOT NULL,
    start_ms BIGINT NOT NULL CHECK (start_ms >= 0),
    end_ms BIGINT NOT NULL CHECK (end_ms > start_ms),
    text TEXT NOT NULL CHECK (length(btrim(text)) > 0),
    confidence DOUBLE PRECISION CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    speaker_id UUID,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    PRIMARY KEY (tenant_id, transcript_id, revision, segment_id),
    CONSTRAINT fk_segment_transcript
        FOREIGN KEY (tenant_id, transcript_id, revision)
        REFERENCES transcripts (tenant_id, transcript_id, revision) ON DELETE CASCADE,
    CONSTRAINT fk_segment_speaker
        FOREIGN KEY (tenant_id, transcript_id, revision, speaker_id)
        REFERENCES transcript_speakers (tenant_id, transcript_id, revision, speaker_id)
);

CREATE INDEX idx_transcript_segments_search
    ON transcript_segments USING GIN (search_vector);
CREATE INDEX idx_transcript_segments_tenant_time
    ON transcript_segments (tenant_id, transcript_id, revision, start_ms);

CREATE TABLE transcript_words (
    tenant_id UUID NOT NULL,
    transcript_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    segment_id UUID NOT NULL,
    word_index INTEGER NOT NULL CHECK (word_index >= 0),
    start_ms BIGINT NOT NULL CHECK (start_ms >= 0),
    end_ms BIGINT NOT NULL CHECK (end_ms > start_ms),
    text TEXT NOT NULL CHECK (length(btrim(text)) > 0),
    confidence DOUBLE PRECISION CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    PRIMARY KEY (tenant_id, transcript_id, revision, segment_id, word_index),
    CONSTRAINT fk_word_segment
        FOREIGN KEY (tenant_id, transcript_id, revision, segment_id)
        REFERENCES transcript_segments (tenant_id, transcript_id, revision, segment_id) ON DELETE CASCADE
);

CREATE INDEX idx_transcript_words_tenant_time
    ON transcript_words (tenant_id, transcript_id, revision, start_ms);

ALTER TABLE transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcripts FORCE ROW LEVEL SECURITY;
CREATE POLICY transcripts_tenant_isolation ON transcripts
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

ALTER TABLE transcript_speakers ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_speakers FORCE ROW LEVEL SECURITY;
CREATE POLICY transcript_speakers_tenant_isolation ON transcript_speakers
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

ALTER TABLE transcript_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_segments FORCE ROW LEVEL SECURITY;
CREATE POLICY transcript_segments_tenant_isolation ON transcript_segments
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

ALTER TABLE transcript_words ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_words FORCE ROW LEVEL SECURITY;
CREATE POLICY transcript_words_tenant_isolation ON transcript_words
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

-- Application role receives append/read access only. No UPDATE/DELETE prevents
-- rewriting history; parent asset deletion remains available to retention workers.
-- Cascading FK actions perform source-linked deletion under PostgreSQL's FK machinery.
