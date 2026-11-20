ALTER TABLE ingestion_jobs
    ADD COLUMN started_at TIMESTAMPTZ;

CREATE INDEX idx_ingestion_jobs_tenant_state_created
    ON ingestion_jobs (tenant_id, state, created_at);
