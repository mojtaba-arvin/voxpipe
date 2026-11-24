"""Explicit environment-driven construction for speech resilience.

The process-local breaker remains the safe default. Multi-worker deployments
can opt into PostgreSQL coordination with VOXPIPE_CIRCUIT_BREAKER_BACKEND=postgres.
A requested PostgreSQL backend never silently degrades to process-local state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from voxpipe.speech.circuit_breaker import CircuitBreakerPort, ProviderCircuitBreaker
from voxpipe.speech.ports import SpeechToTextPort
from voxpipe.speech.postgres_circuit_breaker import PostgresProviderCircuitBreaker
from voxpipe.speech.resilience import CircuitBreakingSpeechToTextPort


@dataclass(frozen=True, slots=True)
class SpeechResilienceRuntime:
    """The guarded adapter and its breaker, owned by one application process."""

    speech_port: CircuitBreakingSpeechToTextPort
    breaker: CircuitBreakerPort
    backend: str


def _positive_int(environment: Mapping[str, str], name: str, default: int, *, maximum: int) -> int:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _cooldown(environment: Mapping[str, str]) -> float:
    raw = environment.get("VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS")
    if raw is None or not raw.strip():
        return 30.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS must be numeric") from None
    if not 1.0 <= value <= 3600.0 or value != value or value in (float("inf"), float("-inf")):
        raise ValueError("VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS must be finite and in [1,3600]")
    return value


def build_speech_resilience_runtime(
    adapter: SpeechToTextPort,
    *,
    environ: Mapping[str, str] | None = None,
) -> SpeechResilienceRuntime:
    """Build a guarded speech adapter from explicit, validated deployment config.

    Supported backends are process (default) and postgres. PostgreSQL uses
    VOXPIPE_DATABASE_DSN and fails closed when unavailable or misconfigured.
    """
    environment = os.environ if environ is None else environ
    backend = environment.get("VOXPIPE_CIRCUIT_BREAKER_BACKEND", "process").strip().lower()
    if backend not in {"process", "postgres"}:
        raise ValueError("VOXPIPE_CIRCUIT_BREAKER_BACKEND must be 'process' or 'postgres'")

    threshold = _positive_int(
        environment, "VOXPIPE_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3, maximum=100,
    )
    cooldown = _cooldown(environment)
    if backend == "postgres":
        dsn = environment.get("VOXPIPE_DATABASE_DSN", "").strip()
        if not dsn:
            raise RuntimeError(
                "PostgreSQL circuit breaker requested but VOXPIPE_DATABASE_DSN is not configured"
            )
        breaker: CircuitBreakerPort = PostgresProviderCircuitBreaker(
            dsn, failure_threshold=threshold, cooldown_seconds=cooldown,
        )
    else:
        breaker = ProviderCircuitBreaker(
            failure_threshold=threshold, cooldown_seconds=cooldown,
        )

    return SpeechResilienceRuntime(
        speech_port=CircuitBreakingSpeechToTextPort(adapter, breaker=breaker),
        breaker=breaker,
        backend=backend,
    )
