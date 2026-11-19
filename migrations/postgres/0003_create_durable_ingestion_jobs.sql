ALTER TABLE ingestion_sessions
    ADD CONSTRAINT uq_ingestion_sessions_tenant_session
    UNIQUE (tenant_id, session_id);

CREATE TABLE ingestion_jobs (
    job_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    session_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'leased', 'completed', 'dead', 'cancelled')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner UUID,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ingestion_jobs_tenant_session UNIQUE (tenant_id, session_id),
    CONSTRAINT fk_ingestion_jobs_tenant_session
        FOREIGN KEY (tenant_id, session_id)
        REFERENCES ingestion_sessions (tenant_id, session_id),
    CONSTRAINT ck_ingestion_jobs_lease_state CHECK (
        (state = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (state <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    )
);

CREATE INDEX idx_ingestion_jobs_claim
    ON ingestion_jobs (tenant_id, state, available_at, created_at);

CREATE INDEX idx_ingestion_jobs_expired_lease
    ON ingestion_jobs (tenant_id, lease_expires_at)
    WHERE state = 'leased';

ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_jobs FORCE ROW LEVEL SECURITY;

CREATE POLICY ingestion_jobs_tenant_isolation ON ingestion_jobs
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);
