-- Expire half-open probe ownership so a crashed worker cannot wedge a provider.
ALTER TABLE provider_circuit_breakers
    ADD COLUMN probe_expires_at TIMESTAMPTZ;

-- Preserve any in-flight probe during an online migration, but bound its lifetime.
UPDATE provider_circuit_breakers
SET probe_expires_at = now() + interval '5 minutes'
WHERE probe_token IS NOT NULL;

ALTER TABLE provider_circuit_breakers
    ADD CONSTRAINT ck_provider_breaker_probe_expiry_pair CHECK (
        (probe_token IS NULL AND probe_expires_at IS NULL)
        OR (probe_token IS NOT NULL AND probe_expires_at IS NOT NULL)
    );

CREATE INDEX idx_provider_circuit_breakers_probe_expiry
    ON provider_circuit_breakers (probe_expires_at)
    WHERE probe_token IS NOT NULL;
