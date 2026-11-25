-- Tenant-scoped worker liveness and progress metadata. No job, asset or tenant
-- identifiers are exposed by the operations API; only aggregate counts are returned.
CREATE TABLE ingestion_worker_heartbeats (
    tenant_id UUID NOT NULL,
    worker_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('idle', 'busy', 'stopped')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_job_started_at TIMESTAMPTZ,
    last_job_completed_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, worker_id),
    CONSTRAINT ck_ingestion_worker_stopped_state CHECK (
        (state = 'stopped' AND stopped_at IS NOT NULL)
        OR (state <> 'stopped' AND stopped_at IS NULL)
    )
);

CREATE INDEX idx_ingestion_worker_heartbeats_tenant_seen
    ON ingestion_worker_heartbeats (tenant_id, heartbeat_at DESC)
    WHERE state <> 'stopped';

ALTER TABLE ingestion_worker_heartbeats ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_worker_heartbeats FORCE ROW LEVEL SECURITY;
CREATE POLICY ingestion_worker_heartbeats_tenant_isolation
    ON ingestion_worker_heartbeats
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);
