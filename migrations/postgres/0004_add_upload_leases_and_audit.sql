CREATE TABLE ingestion_upload_leases (
    tenant_id UUID NOT NULL,
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, session_id),
    CONSTRAINT fk_upload_leases_tenant_session
        FOREIGN KEY (tenant_id, session_id)
        REFERENCES ingestion_sessions (tenant_id, session_id)
);

CREATE INDEX idx_ingestion_upload_leases_expiry
    ON ingestion_upload_leases (tenant_id, lease_expires_at);

ALTER TABLE ingestion_upload_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_upload_leases FORCE ROW LEVEL SECURITY;
CREATE POLICY ingestion_upload_leases_tenant_isolation ON ingestion_upload_leases
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

CREATE TABLE ingestion_audit_events (
    event_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    session_id UUID,
    asset_id UUID,
    event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 64),
    outcome TEXT NOT NULL CHECK (length(outcome) BETWEEN 1 AND 32),
    error_code TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_audit_tenant_session
        FOREIGN KEY (tenant_id, session_id)
        REFERENCES ingestion_sessions (tenant_id, session_id)
);

CREATE INDEX idx_ingestion_audit_tenant_occurred
    ON ingestion_audit_events (tenant_id, occurred_at DESC);
CREATE INDEX idx_ingestion_audit_session_occurred
    ON ingestion_audit_events (tenant_id, session_id, occurred_at DESC)
    WHERE session_id IS NOT NULL;

ALTER TABLE ingestion_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY ingestion_audit_events_tenant_isolation ON ingestion_audit_events
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

CREATE INDEX idx_ingestion_sessions_expiry
    ON ingestion_sessions (tenant_id, state, (payload->>'expires_at'))
    WHERE state IN ('created', 'receiving');

CREATE INDEX idx_audio_assets_retention_expiry
    ON audio_assets (tenant_id, (payload->>'retention_expires_at'))
    WHERE payload ? 'retention_expires_at';
