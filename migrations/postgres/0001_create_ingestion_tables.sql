CREATE TABLE ingestion_sessions (
    session_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('created', 'receiving', 'finalizing', 'queued', 'processing',
                  'completed', 'failed', 'cancelled', 'expired')
    ),
    payload JSONB NOT NULL,
    CONSTRAINT uq_ingestion_sessions_tenant_idempotency
        UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE audio_assets (
    asset_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    payload JSONB NOT NULL,
    CONSTRAINT uq_audio_assets_tenant_asset
        UNIQUE (tenant_id, asset_id)
);
