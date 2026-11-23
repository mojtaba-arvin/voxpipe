"""Circuit-breaker adapter using VoxPipe's shared routing breaker.

Retries are intentionally not implicit: hosted STT requests may be billable and
some vendors do not provide idempotency guarantees. Durable orchestration must
own retries after provider-specific idempotency and request reconciliation are
verified. Pass the same ProviderCircuitBreaker used by AdaptiveModelRouter so
transcription admission and routing observe one process-local state.
"""
from __future__ import annotations

from voxpipe.speech.circuit_breaker import ProviderCircuitBreaker
from voxpipe.speech.contracts import (
    Transcript,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
)
from voxpipe.speech.ports import SpeechToTextPort


class CircuitBreakingSpeechToTextPort:
    """Guard one adapter with the repository's fenced provider circuit breaker."""

    def __init__(
        self,
        adapter: SpeechToTextPort,
        *,
        breaker: ProviderCircuitBreaker,
    ) -> None:
        if not isinstance(breaker, ProviderCircuitBreaker):
            raise TypeError("breaker must be a ProviderCircuitBreaker")
        self._adapter = adapter
        self._breaker = breaker

    @property
    def provider_name(self) -> str:
        return self._adapter.provider_name

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        permit = self._breaker.try_acquire(self.provider_name)
        if permit is None:
            raise TranscriptionError(
                TranscriptionErrorCode.PROVIDER_UNAVAILABLE,
                provider=self.provider_name,
                retryable=True,
                retry_after_seconds=max(1, int(self._breaker.cooldown_seconds + 0.999)),
            )
        try:
            result = self._adapter.transcribe(request)
        except TranscriptionError as exc:
            if exc.retryable:
                self._breaker.record_failure(permit)
            else:
                # A deterministic rejection still proves that the provider
                # endpoint answered; it is not an availability failure.
                self._breaker.record_success(permit)
            raise
        except Exception:
            self._breaker.record_failure(permit)
            raise
        else:
            self._breaker.record_success(permit)
            return result
