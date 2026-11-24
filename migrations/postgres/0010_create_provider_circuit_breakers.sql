-- Provider availability is system-wide coordination state, not tenant data.
-- It stores only a bounded provider key and breaker counters/timestamps/tokens.
CREATE TABLE provider_circuit_breakers (
    provider TEXT PRIMARY KEY CHECK (length(provider) BETWEEN 1 AND 128),
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    opened_at TIMESTAMPTZ,
    probe_token TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_provider_breaker_probe_requires_open CHECK (
        probe_token IS NULL OR opened_at IS NOT NULL
    )
);

CREATE INDEX idx_provider_circuit_breakers_opened
    ON provider_circuit_breakers (opened_at)
    WHERE opened_at IS NOT NULL;
