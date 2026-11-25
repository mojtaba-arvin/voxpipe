"""PostgreSQL-coordinated provider circuit breaker for multi-worker runtimes.

The table contains only system-wide provider availability metadata. It is not
tenant data and deliberately contains no request, audio, transcript or secret
fields. Row locks serialize half-open probe acquisition across processes.
"""
from __future__ import annotations

import math
import secrets
from contextlib import contextmanager

from voxpipe.speech.circuit_breaker import CircuitPermit


class PostgresProviderCircuitBreaker:
    """Shared provider breaker with transactionally fenced half-open probes."""

    # A crashed half-open worker must not strand a provider indefinitely.
    # Keep this longer than the configured hard timeout of any admitted request.
    PROBE_LEASE_SECONDS = 300

    def __init__(
        self, dsn: str, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0,
    ) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        if (not isinstance(failure_threshold, int) or isinstance(failure_threshold, bool)
                or not 1 <= failure_threshold <= 100):
            raise ValueError("failure_threshold must be an integer in [1,100]")
        if (not isinstance(cooldown_seconds, (int, float)) or isinstance(cooldown_seconds, bool)
                or not math.isfinite(cooldown_seconds) or not 1.0 <= cooldown_seconds <= 3600.0):
            raise ValueError("cooldown_seconds must be finite and in [1,3600]")
        self._dsn = dsn
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)

    @staticmethod
    def _provider(provider: str) -> str:
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 128:
            raise ValueError("provider must be a non-empty bounded identifier")
        return provider

    @contextmanager
    def _connection(self):
        import psycopg
        with psycopg.connect(self._dsn) as connection:
            yield connection

    def try_acquire(self, provider: str) -> CircuitPermit | None:
        name = self._provider(provider)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO provider_circuit_breakers (provider) VALUES (%s) ON CONFLICT DO NOTHING",
                (name,),
            )
            row = connection.execute(
                "SELECT opened_at, probe_token, probe_expires_at FROM provider_circuit_breakers "
                "WHERE provider=%s FOR UPDATE",
                (name,),
            ).fetchone()
            if row is None:
                raise RuntimeError("provider circuit row disappeared during acquisition")
            opened_at, probe_token, probe_expires_at = row
            if opened_at is None:
                return CircuitPermit(name)
            eligible = connection.execute(
                "SELECT %s::timestamptz + (%s * interval '1 second') <= now()",
                (opened_at, self.cooldown_seconds),
            ).fetchone()[0]
            if not eligible:
                return None
            if probe_token is not None and probe_expires_at is not None:
                active = connection.execute(
                    "SELECT %s::timestamptz > now()", (probe_expires_at,),
                ).fetchone()[0]
                if active:
                    return None
            token = secrets.token_urlsafe(24)
            connection.execute(
                """
                UPDATE provider_circuit_breakers
                SET probe_token=%s,
                    probe_expires_at=now() + (%s * interval '1 second'),
                    updated_at=now()
                WHERE provider=%s
                """,
                (token, self.PROBE_LEASE_SECONDS, name),
            )
            return CircuitPermit(name, token)

    def record_success(self, permit: CircuitPermit) -> bool:
        if not isinstance(permit, CircuitPermit):
            raise TypeError("permit must be a CircuitPermit")
        with self._connection() as connection:
            if permit.is_probe:
                row = connection.execute(
                    "DELETE FROM provider_circuit_breakers "
                    "WHERE provider=%s AND opened_at IS NOT NULL AND probe_token=%s "
                    "RETURNING provider",
                    (permit.provider, permit.probe_token),
                ).fetchone()
            else:
                row = connection.execute(
                    "DELETE FROM provider_circuit_breakers "
                    "WHERE provider=%s AND opened_at IS NULL RETURNING provider",
                    (permit.provider,),
                ).fetchone()
        return row is not None

    def record_failure(self, permit: CircuitPermit) -> bool:
        if not isinstance(permit, CircuitPermit):
            raise TypeError("permit must be a CircuitPermit")
        with self._connection() as connection:
            if permit.is_probe:
                row = connection.execute(
                    """
                    UPDATE provider_circuit_breakers
                    SET consecutive_failures=GREATEST(consecutive_failures + 1, %s),
                        opened_at=now(), probe_token=NULL, probe_expires_at=NULL, updated_at=now()
                    WHERE provider=%s AND opened_at IS NOT NULL AND probe_token=%s
                    RETURNING provider
                    """,
                    (self.failure_threshold, permit.provider, permit.probe_token),
                ).fetchone()
                return row is not None
            connection.execute(
                "INSERT INTO provider_circuit_breakers (provider) VALUES (%s) ON CONFLICT DO NOTHING",
                (permit.provider,),
            )
            row = connection.execute(
                """
                UPDATE provider_circuit_breakers
                SET consecutive_failures=consecutive_failures + 1,
                    opened_at=CASE WHEN consecutive_failures + 1 >= %s THEN now() ELSE NULL END,
                    updated_at=now()
                WHERE provider=%s AND opened_at IS NULL
                RETURNING opened_at IS NOT NULL
                """,
                (self.failure_threshold, permit.provider),
            ).fetchone()
        return bool(row and row[0])

    def release_probe(self, permit: CircuitPermit) -> bool:
        if not isinstance(permit, CircuitPermit):
            raise TypeError("permit must be a CircuitPermit")
        if not permit.is_probe:
            return False
        with self._connection() as connection:
            row = connection.execute(
                "UPDATE provider_circuit_breakers "
                "SET probe_token=NULL, probe_expires_at=NULL, updated_at=now() "
                "WHERE provider=%s AND opened_at IS NOT NULL AND probe_token=%s "
                "RETURNING provider",
                (permit.provider, permit.probe_token),
            ).fetchone()
        return row is not None

    def open_providers(self) -> frozenset[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT provider FROM provider_circuit_breakers
                WHERE opened_at IS NOT NULL
                  AND (opened_at + (%s * interval '1 second') > now()
                       OR (probe_token IS NOT NULL AND probe_expires_at > now()))
                ORDER BY provider
                """,
                (self.cooldown_seconds,),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def snapshot(self) -> dict[str, dict[str, int | str | bool | None]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT provider, consecutive_failures, opened_at, probe_token "
                "FROM provider_circuit_breakers ORDER BY provider"
            ).fetchall()
        return {
            provider: {
                "consecutive_failures": failures,
                "opened_at": opened_at.isoformat() if opened_at is not None else None,
                "probe_in_flight": probe_token is not None,
            }
            for provider, failures, opened_at, probe_token in rows
        }
