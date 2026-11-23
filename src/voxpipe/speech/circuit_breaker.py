"""Process-local provider circuit breaker for adaptive routing.

For multi-process deployments, back this state with a shared store; this class
intentionally makes no cross-process coordination claim.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class ProviderCircuitBreaker:
    """Consecutive-failure breaker with one half-open probe after cooldown."""

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

    def try_acquire(self, provider: str, *, now: float | None = None) -> bool:
        """Allow a closed provider or exactly one half-open probe after cooldown."""
        name, instant = self._provider(provider), self._now(now)
        with self._lock:
            circuit = self._circuits.get(name)
            if circuit is None or circuit.opened_at is None:
                return True
            if instant - circuit.opened_at < self.cooldown_seconds or circuit.probe_in_flight:
                return False
            circuit.probe_in_flight = True
            return True

    def record_success(self, provider: str) -> None:
        name = self._provider(provider)
        with self._lock:
            self._circuits.pop(name, None)

    def record_failure(self, provider: str, *, now: float | None = None) -> None:
        name, instant = self._provider(provider), self._now(now)
        with self._lock:
            circuit = self._circuits.setdefault(name, _Circuit())
            circuit.failures += 1
            if circuit.opened_at is not None or circuit.failures >= self.failure_threshold:
                circuit.failures = max(circuit.failures, self.failure_threshold)
                circuit.opened_at = instant
                circuit.probe_in_flight = False

    def release_probe(self, provider: str) -> None:
        """Release a half-open probe reservation when a request was cancelled pre-dispatch."""
        name = self._provider(provider)
        with self._lock:
            circuit = self._circuits.get(name)
            if circuit is not None and circuit.opened_at is not None:
                circuit.probe_in_flight = False

    def open_providers(self, *, now: float | None = None) -> frozenset[str]:
        """Return providers to exclude from ordinary routing.

        A caller that successfully acquires the single half-open probe with
        try_acquire() may route that one request directly; all other callers
        must continue excluding the provider returned here.
        """
        instant = self._now(now)
        with self._lock:
            return frozenset(
                name for name, circuit in self._circuits.items()
                if circuit.opened_at is not None and (
                    instant - circuit.opened_at < self.cooldown_seconds or circuit.probe_in_flight
                )
            )

    def snapshot(self) -> dict[str, dict[str, int | float | bool | None]]:
        """Return process-local diagnostic state without request payloads."""
        with self._lock:
            return {
                name: {
                    "consecutive_failures": circuit.failures,
                    "opened_at_monotonic": circuit.opened_at,
                    "probe_in_flight": circuit.probe_in_flight,
                }
                for name, circuit in sorted(self._circuits.items())
            }
