ALTER TABLE ingestion_sessions
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE audio_assets
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX idx_ingestion_sessions_tenant_state_updated
    ON ingestion_sessions (tenant_id, state, updated_at);

ALTER TABLE ingestion_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE audio_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE audio_assets FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ingestion_sessions_tenant_isolation ON ingestion_sessions;
CREATE POLICY ingestion_sessions_tenant_isolation ON ingestion_sessions
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

DROP POLICY IF EXISTS audio_assets_tenant_isolation ON audio_assets;
CREATE POLICY audio_assets_tenant_isolation ON audio_assets
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);
