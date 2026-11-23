"""Provider-level circuit breaking for speech adapters.

Retries are intentionally not implicit: hosted STT requests may be billable and
some vendors do not provide idempotency guarantees. Durable orchestration must
own retries after provider-specific idempotency and replayability are verified.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from voxpipe.speech.contracts import (
    Transcript,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
)
from voxpipe.speech.ports import SpeechToTextPort


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerSnapshot:
    state: CircuitState
    consecutive_failures: int
    retry_after_seconds: int | None


class CircuitBreakingSpeechToTextPort:
    """A thread-safe, process-local circuit breaker around one provider.

    Only retryable provider failures count toward opening the circuit.
    Non-retryable request/feature errors prove the provider responded and do
    not poison availability. Exactly one half-open probe is admitted.
    """

    def __init__(
        self,
        adapter: SpeechToTextPort,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        self._adapter = adapter
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def provider_name(self) -> str:
        return self._adapter.provider_name

    def snapshot(self) -> CircuitBreakerSnapshot:
        with self._lock:
            if self._opened_at is None:
                return CircuitBreakerSnapshot(CircuitState.CLOSED, self._failures, None)
            remaining = self._recovery_timeout - (self._clock() - self._opened_at)
            if remaining > 0:
                return CircuitBreakerSnapshot(
                    CircuitState.OPEN, self._failures, max(1, int(remaining + 0.999))
                )
            return CircuitBreakerSnapshot(CircuitState.HALF_OPEN, self._failures, 0)

    def _admit(self) -> bool:
        """Return True for the single half-open probe."""
        with self._lock:
            if self._opened_at is None:
                return False
            if self._clock() - self._opened_at < self._recovery_timeout:
                remaining = self._recovery_timeout - (self._clock() - self._opened_at)
                raise TranscriptionError(
                    TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                    provider=self.provider_name,
                    retryable=True,
                    retry_after_seconds=max(1, int(remaining + 0.999)),
                )
            if self._probe_in_flight:
                raise TranscriptionError(
                    TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                    provider=self.provider_name,
                    retryable=True,
                    retry_after_seconds=1,
                )
            self._probe_in_flight = True
            return True

    def _succeeded(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def _failed(self, *, probe: bool) -> None:
        with self._lock:
            self._failures += 1
            if probe or self._failures >= self._failure_threshold:
                self._opened_at = self._clock()
            self._probe_in_flight = False

    def _neutral(self, *, probe: bool) -> None:
        # A non-retryable response means the provider is reachable.
        if probe:
            self._succeeded()

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        probe = self._admit()
        try:
            result = self._adapter.transcribe(request)
        except TranscriptionError as exc:
            if exc.retryable:
                self._failed(probe=probe)
            else:
                self._neutral(probe=probe)
            raise
        except Exception:
            self._failed(probe=probe)
            raise
        self._succeeded()
        return result
