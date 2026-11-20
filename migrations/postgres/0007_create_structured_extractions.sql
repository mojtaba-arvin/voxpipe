-- Append-only structured output bound to immutable tenant transcript revisions.
CREATE TABLE structured_extractions (
 tenant_id UUID NOT NULL,
 extraction_id UUID NOT NULL,
 document_id UUID NOT NULL,
 asset_id UUID NOT NULL,
 transcript_id UUID NOT NULL,
 transcript_revision INTEGER NOT NULL CHECK (transcript_revision>=1),
 idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
 result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,extraction_id),
 UNIQUE (tenant_id,idempotency_key),
 FOREIGN KEY (tenant_id,transcript_id,transcript_revision)
   REFERENCES transcripts(tenant_id,transcript_id,revision) ON DELETE CASCADE,
 FOREIGN KEY (tenant_id,asset_id)
   REFERENCES audio_assets(tenant_id,asset_id) ON DELETE CASCADE
);
CREATE INDEX idx_structured_extractions_source ON structured_extractions(tenant_id,transcript_id,transcript_revision,created_at DESC);
ALTER TABLE structured_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE structured_extractions FORCE ROW LEVEL SECURITY;
CREATE POLICY structured_extractions_tenant_isolation ON structured_extractions
 USING (tenant_id=nullif(current_setting('voxpipe.tenant_id',true),'')::uuid)
 WITH CHECK (tenant_id=nullif(current_setting('voxpipe.tenant_id',true),'')::uuid);
