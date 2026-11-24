"""Process-local provider circuit breaker for adaptive routing.

For multi-process deployments, back this state with a shared store; this class
intentionally makes no cross-process coordination claim.
"""
from __future__ import annotations

import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    """Admission returned by try_acquire; half-open permits carry a fencing token."""

    provider: str
    probe_token: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip() or len(self.provider) > 128:
            raise ValueError("provider must be a non-empty bounded identifier")
        if self.probe_token is not None and (
            not isinstance(self.probe_token, str) or not self.probe_token.strip() or len(self.probe_token) > 256
        ):
            raise ValueError("probe_token must be a non-empty bounded string or null")

    @property
    def is_probe(self) -> bool:
        return self.probe_token is not None


@runtime_checkable
class CircuitBreakerPort(Protocol):
    """Structural contract shared by local and distributed breaker stores."""

    cooldown_seconds: float

    def try_acquire(self, provider: str) -> CircuitPermit | None: ...

    def record_success(self, permit: CircuitPermit) -> bool: ...

    def record_failure(self, permit: CircuitPermit) -> bool: ...


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    opened_at: float | None = None
    probe_token: str | None = None


class ProviderCircuitBreaker:
    """Consecutive-failure breaker with one fenced half-open probe after cooldown."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock=time.monotonic,
    ) -> None:
        if (not isinstance(failure_threshold, int) or isinstance(failure_threshold, bool)
                or not 1 <= failure_threshold <= 100):
            raise ValueError("failure_threshold must be an integer in [1,100]")
        if (not isinstance(cooldown_seconds, (int, float)) or isinstance(cooldown_seconds, bool)
                or not math.isfinite(cooldown_seconds) or not 1.0 <= cooldown_seconds <= 3600.0):
            raise ValueError("cooldown_seconds must be finite and in [1,3600]")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._circuits: dict[str, _Circuit] = {}

    @staticmethod
    def _provider(provider: str) -> str:
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 128:
            raise ValueError("provider must be a non-empty bounded identifier")
        return provider

    def _now(self, now: float | None) -> float:
        value = self._clock() if now is None else now
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError("circuit clock value must be finite")
        return float(value)

    def try_acquire(self, provider: str, *, now: float | None = None) -> CircuitPermit | None:
        """Return a permit for closed providers or one fenced probe after cooldown."""
        name, instant = self._provider(provider), self._now(now)
        with self._lock:
            circuit = self._circuits.get(name)
            if circuit is None or circuit.opened_at is None:
                return CircuitPermit(name)
            if instant - circuit.opened_at < self.cooldown_seconds or circuit.probe_token is not None:
                return None
            token = secrets.token_urlsafe(18)
            circuit.probe_token = token
            return CircuitPermit(name, token)

    def record_success(self, permit: CircuitPermit) -> bool:
        """Record success; stale non-probe calls cannot close a newly opened circuit."""
        if not isinstance(permit, CircuitPermit):
            raise TypeError("permit must be a CircuitPermit")
        with self._lock:
            circuit = self._circuits.get(permit.provider)
            if permit.is_probe:
                if circuit is None or circuit.probe_token != permit.probe_token:
                    return False
                self._circuits.pop(permit.provider, None)
                return True
            if circuit is not None and circuit.opened_at is not None:
                return False
            self._circuits.pop(permit.provider, None)
            return True

    def record_failure(self, permit: CircuitPermit, *, now: float | None = None) -> bool:
        """Record failure; only the current probe permit may reopen a half-open circuit."""
        if not isinstance(permit, CircuitPermit):
            raise TypeError("permit must be a CircuitPermit")
        instant = self._now(now)
        with self._lock:
            circuit = self._circuits.setdefault(permit.provider, _Circuit())
            if permit.is_probe:
                if circuit.probe_token != permit.probe_token:
                    return False
                circuit.failures = max(circuit.failures + 1, self.failure_threshold)
                circuit.opened_at = instant
                circuit.probe_token = None
                return True
            if circuit.opened_at is not None:
                return False
            circuit.failures += 1
            if circuit.failures >= self.failure_threshold:
                circuit.opened_at = instant
                circuit.probe_token = None
                return True
            return False

    def release_probe(self, permit: CircuitPermit) -> bool:
        """Release an acquired half-open probe when the request is cancelled pre-dispatch."""
        if not isinstance(permit, CircuitPermit):
            raise TypeError("permit must be a CircuitPermit")
        if not permit.is_probe:
            return False
        with self._lock:
            circuit = self._circuits.get(permit.provider)
            if circuit is None or circuit.probe_token != permit.probe_token:
                return False
            circuit.probe_token = None
            return True

    def open_providers(self, *, now: float | None = None) -> frozenset[str]:
        """Return providers that ordinary routing must exclude."""
        instant = self._now(now)
        with self._lock:
            return frozenset(
                name for name, circuit in self._circuits.items()
                if circuit.opened_at is not None and (
                    instant - circuit.opened_at < self.cooldown_seconds or circuit.probe_token is not None
                )
            )

    def snapshot(self) -> dict[str, dict[str, int | float | bool | None]]:
        """Return process-local diagnostic state without request payloads."""
        with self._lock:
            return {
                name: {
                    "consecutive_failures": circuit.failures,
                    "opened_at_monotonic": circuit.opened_at,
                    "probe_in_flight": circuit.probe_token is not None,
                }
                for name, circuit in sorted(self._circuits.items())
            }
