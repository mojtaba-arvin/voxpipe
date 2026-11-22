-- Tenant-scoped durable workflow journal. Step inputs/results contain references and
-- bounded metadata only; raw audio/transcripts are never persisted in this journal.
CREATE TABLE workflow_runs (
    tenant_id UUID NOT NULL,
    workflow_id UUID NOT NULL,
    asset_id UUID,
    workflow_type TEXT NOT NULL CHECK (length(workflow_type) BETWEEN 1 AND 64),
    workflow_version TEXT NOT NULL CHECK (length(workflow_version) BETWEEN 1 AND 64),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    state TEXT NOT NULL CHECK (state IN ('pending','running','waiting_approval','completed','failed','cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, workflow_id),
    UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_workflow_tenant_asset
        FOREIGN KEY (tenant_id, asset_id)
        REFERENCES audio_assets (tenant_id, asset_id) ON DELETE CASCADE
);

CREATE INDEX idx_workflow_runs_tenant_state
    ON workflow_runs (tenant_id, state, updated_at);

CREATE TABLE workflow_steps (
    tenant_id UUID NOT NULL,
    workflow_id UUID NOT NULL,
    step_id UUID NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    step_name TEXT NOT NULL CHECK (length(step_name) BETWEEN 1 AND 128),
    state TEXT NOT NULL CHECK (state IN ('pending','running','waiting_approval','completed','failed','cancelled')),
    input_refs JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(input_refs) = 'object'),
    result_refs JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(result_refs) = 'object'),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 20),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner UUID,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 64),
    approval_decision TEXT CHECK (approval_decision IS NULL OR approval_decision IN ('approved','rejected')),
    approval_actor_id UUID,
    approval_decided_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workflow_id, step_id),
    UNIQUE (tenant_id, workflow_id, ordinal),
    CONSTRAINT fk_workflow_step_run
        FOREIGN KEY (tenant_id, workflow_id)
        REFERENCES workflow_runs (tenant_id, workflow_id) ON DELETE CASCADE,
    CONSTRAINT ck_workflow_step_lease_state CHECK (
        (state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_workflow_step_approval_actor CHECK (
        (approval_decision IS NULL AND approval_actor_id IS NULL AND approval_decided_at IS NULL)
        OR (approval_decision IS NOT NULL AND approval_actor_id IS NOT NULL AND approval_decided_at IS NOT NULL)
    )
);

CREATE INDEX idx_workflow_steps_claim
    ON workflow_steps (tenant_id, workflow_id, state, available_at, ordinal);
CREATE INDEX idx_workflow_steps_expired_lease
    ON workflow_steps (tenant_id, lease_expires_at)
    WHERE state = 'running';

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_runs_tenant_isolation ON workflow_runs
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);

ALTER TABLE workflow_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_steps_tenant_isolation ON workflow_steps
    USING (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('voxpipe.tenant_id', true), '')::uuid);
