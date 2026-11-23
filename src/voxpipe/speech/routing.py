"""Fail-closed, evidence-based adaptive model routing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from voxpipe.speech.capabilities import ProviderCapability, ProviderCapabilityRegistry


class NoEligibleModel(RuntimeError):
    """No explicitly enabled model satisfies the active routing policy."""


@dataclass(frozen=True, slots=True)
class ModelQualityEvidence:
    task: str
    provider: str
    model: str
    model_version: str
    quality_score: float
    latency_ms: int
    available: bool
    measured_at: datetime
    evidence_id: str

    def __post_init__(self) -> None:
        for field in ("task", "provider", "model", "model_version", "evidence_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if (not isinstance(self.quality_score, (int, float)) or isinstance(self.quality_score, bool)
                or not math.isfinite(self.quality_score) or not 0 <= self.quality_score <= 1):
            raise ValueError("quality_score must be finite and between 0 and 1")
        if not isinstance(self.latency_ms, int) or isinstance(self.latency_ms, bool) or self.latency_ms < 1:
            raise ValueError("latency_ms must be a positive integer")
        if not isinstance(self.available, bool):
            raise ValueError("available must be boolean")
        if (not isinstance(self.measured_at, datetime) or self.measured_at.tzinfo is None
                or self.measured_at.utcoffset() is None):
            raise ValueError("measured_at must be timezone-aware")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.task, self.provider, self.model, self.model_version)


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    task: str
    version: str
    allowed_models: frozenset[tuple[str, str, str]]
    quality_floor: float = 0.80
    max_latency_ms: int = 10_000
    max_cost_per_minute_usd: float = 0.0
    require_local: bool = True
    max_evidence_age_seconds: int = 86_400
    max_fallbacks: int = 1

    def __post_init__(self) -> None:
        for field in ("task", "version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if not isinstance(self.allowed_models, frozenset) or not self.allowed_models:
            raise ValueError("allowed_models must be a non-empty explicit allowlist")
        for item in self.allowed_models:
            if (not isinstance(item, tuple) or len(item) != 3
                    or any(not isinstance(value, str) or not value.strip() for value in item)):
                raise ValueError("allowed_models entries must be provider/model/version tuples")
        if (not isinstance(self.quality_floor, (int, float)) or isinstance(self.quality_floor, bool)
                or not math.isfinite(self.quality_floor) or not 0 <= self.quality_floor <= 1):
            raise ValueError("quality_floor must be finite and between 0 and 1")
        if not isinstance(self.max_latency_ms, int) or isinstance(self.max_latency_ms, bool) or self.max_latency_ms < 1:
            raise ValueError("max_latency_ms must be a positive integer")
        if (not isinstance(self.max_cost_per_minute_usd, (int, float))
                or isinstance(self.max_cost_per_minute_usd, bool)
                or not math.isfinite(self.max_cost_per_minute_usd) or self.max_cost_per_minute_usd < 0):
            raise ValueError("max_cost_per_minute_usd must be finite and non-negative")
        if not isinstance(self.require_local, bool):
            raise ValueError("require_local must be boolean")
        if (not isinstance(self.max_evidence_age_seconds, int) or isinstance(self.max_evidence_age_seconds, bool)
                or self.max_evidence_age_seconds < 1):
            raise ValueError("max_evidence_age_seconds must be positive")
        if not isinstance(self.max_fallbacks, int) or isinstance(self.max_fallbacks, bool) or not 0 <= self.max_fallbacks <= 5:
            raise ValueError("max_fallbacks must be between 0 and 5")


@dataclass(frozen=True, slots=True)
class RoutedModel:
    capability: ProviderCapability
    evidence_id: str
    quality_score: float
    latency_ms: int
    estimated_cost_per_minute_usd: float


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    task: str
    policy_version: str
    selected: RoutedModel
    fallbacks: tuple[RoutedModel, ...]
    decided_at: datetime

    def safe_metadata(self) -> dict:
        """Return audit metadata without query text, audio, transcript or tenant identifiers."""
        def serialize(model: RoutedModel) -> dict:
            return {
                "provider": model.capability.provider,
                "model": model.capability.model,
                "model_version": model.capability.model_version,
                "evidence_id": model.evidence_id,
                "quality_score": model.quality_score,
                "latency_ms": model.latency_ms,
                "estimated_cost_per_minute_usd": model.estimated_cost_per_minute_usd,
            }
        return {
            "task": self.task,
            "policy_version": self.policy_version,
            "selected": serialize(self.selected),
            "fallbacks": [serialize(item) for item in self.fallbacks],
            "decided_at": self.decided_at.isoformat(),
        }


class AdaptiveModelRouter:
    """Choose only allowlisted models with fresh measured quality and availability."""

    def __init__(self, registry: ProviderCapabilityRegistry,
                 evidence: Iterable[ModelQualityEvidence]) -> None:
        if not isinstance(registry, ProviderCapabilityRegistry):
            raise TypeError("registry must be a ProviderCapabilityRegistry")
        self._registry = registry
        self._evidence: dict[tuple[str, str, str, str], ModelQualityEvidence] = {}
        for item in evidence:
            if not isinstance(item, ModelQualityEvidence):
                raise TypeError("evidence entries must be ModelQualityEvidence")
            if item.key in self._evidence:
                raise ValueError("duplicate model quality evidence key")
            self._evidence[item.key] = item

    def route(self, policy: RoutingPolicy, *, language: str,
              required_features: Iterable[str] = (), audio_duration_ms: int | None = None,
              audio_bytes: int | None = None, open_circuit_providers: Iterable[str] = (),
              now: datetime | None = None) -> RoutingDecision:
        if not isinstance(policy, RoutingPolicy):
            raise TypeError("policy must be a RoutingPolicy")
        instant = now or datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language is required")
        if isinstance(open_circuit_providers, (str, bytes)):
            raise ValueError("open_circuit_providers must be an iterable of provider identifiers")
        try:
            open_circuits = frozenset(open_circuit_providers)
        except TypeError:
            raise ValueError("open_circuit_providers must be an iterable of provider identifiers") from None
        if any(not isinstance(provider, str) or not provider.strip() for provider in open_circuits):
            raise ValueError("open_circuit_providers must contain non-empty provider identifiers")
        capabilities = self._registry.find(
            language=language, required_features=required_features,
            require_local=policy.require_local,
            max_cost_per_minute_usd=policy.max_cost_per_minute_usd,
            audio_duration_ms=audio_duration_ms, audio_bytes=audio_bytes,
        )
        eligible: list[RoutedModel] = []
        oldest = instant - timedelta(seconds=policy.max_evidence_age_seconds)
        for capability in capabilities:
            model_key = (capability.provider, capability.model, capability.model_version)
            if model_key not in policy.allowed_models or capability.provider in open_circuits:
                continue
            evidence = self._evidence.get((policy.task, *model_key))
            if evidence is None or not evidence.available:
                continue
            if evidence.measured_at > instant or evidence.measured_at < oldest:
                continue
            if evidence.quality_score < policy.quality_floor or evidence.latency_ms > policy.max_latency_ms:
                continue
            cost = capability.estimated_cost_per_minute_usd
            if cost is None or cost > policy.max_cost_per_minute_usd:
                continue
            eligible.append(RoutedModel(capability, evidence.evidence_id,
                evidence.quality_score, evidence.latency_ms, float(cost)))
        eligible.sort(key=lambda item: (
            -item.quality_score, item.latency_ms, item.estimated_cost_per_minute_usd,
            item.capability.provider, item.capability.model, item.capability.model_version,
        ))
        if not eligible:
            raise NoEligibleModel("no model satisfies the explicit routing policy")
        return RoutingDecision(policy.task, policy.version, eligible[0],
            tuple(eligible[1:1 + policy.max_fallbacks]), instant)
