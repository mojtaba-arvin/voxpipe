CREATE INDEX idx_ingestion_sessions_tenant_updated_at
    ON ingestion_sessions (tenant_id, updated_at DESC);

CREATE INDEX idx_ingestion_jobs_tenant_created_at
    ON ingestion_jobs (tenant_id, created_at DESC);
