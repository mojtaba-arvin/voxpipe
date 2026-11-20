"""Explicit provider/model capability metadata and deterministic registry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider: str
    model: str
    model_version: str
    supported_languages: frozenset[str]
    features: frozenset[str]
    local: bool
    max_audio_duration_ms: int | None = None
    max_audio_bytes: int | None = None
    estimated_cost_per_minute_usd: float | None = None

    def __post_init__(self) -> None:
        for field in ("provider", "model", "model_version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if not isinstance(self.local, bool):
            raise ValueError("local must be boolean")
        if any(not isinstance(x, str) or not x.strip() for x in self.supported_languages):
            raise ValueError("supported_languages must contain non-empty language tags")
        if any(not isinstance(x, str) or not x.strip() for x in self.features):
            raise ValueError("features must contain non-empty identifiers")
        for field in ("max_audio_duration_ms", "max_audio_bytes"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"{field} must be a positive integer or null")
        cost = self.estimated_cost_per_minute_usd
        if cost is not None and (
            not isinstance(cost, (int, float)) or isinstance(cost, bool)
            or not math.isfinite(cost) or cost < 0
        ):
            raise ValueError("estimated cost must be a finite non-negative number or null")


class ProviderCapabilityRegistry:
    """In-memory registry; capability claims are supplied by trusted deployment config."""

    def __init__(self, capabilities: Iterable[ProviderCapability] = ()) -> None:
        self._capabilities: dict[tuple[str, str, str], ProviderCapability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: ProviderCapability) -> None:
        if not isinstance(capability, ProviderCapability):
            raise TypeError("capability must be a ProviderCapability")
        key = (capability.provider, capability.model, capability.model_version)
        if key in self._capabilities:
            raise ValueError("provider/model/version capability is already registered")
        self._capabilities[key] = capability

    def get(self, provider: str, model: str, model_version: str) -> ProviderCapability | None:
        return self._capabilities.get((provider, model, model_version))

    def find(
        self,
        *,
        language: str | None = None,
        required_features: Iterable[str] = (),
        require_local: bool | None = None,
        max_cost_per_minute_usd: float | None = None,
        audio_duration_ms: int | None = None,
        audio_bytes: int | None = None,
    ) -> tuple[ProviderCapability, ...]:
        """Return deterministic candidates; unknown cost never satisfies a cost ceiling."""
        features = frozenset(required_features)
        if require_local is not None and not isinstance(require_local, bool):
            raise ValueError("require_local must be boolean or null")
        if max_cost_per_minute_usd is not None and (
            not isinstance(max_cost_per_minute_usd, (int, float))
            or isinstance(max_cost_per_minute_usd, bool)
            or not math.isfinite(max_cost_per_minute_usd)
            or max_cost_per_minute_usd < 0
        ):
            raise ValueError("max cost must be a finite non-negative number")
        for field, value in (("audio_duration_ms", audio_duration_ms), ("audio_bytes", audio_bytes)):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"{field} must be a positive integer")
        matches = []
        for capability in self._capabilities.values():
            if language is not None and language not in capability.supported_languages:
                continue
            if not features.issubset(capability.features):
                continue
            if require_local is not None and capability.local is not require_local:
                continue
            if max_cost_per_minute_usd is not None and (
                capability.estimated_cost_per_minute_usd is None
                or capability.estimated_cost_per_minute_usd > max_cost_per_minute_usd
            ):
                continue
            if audio_duration_ms is not None and (
                capability.max_audio_duration_ms is not None
                and audio_duration_ms > capability.max_audio_duration_ms
            ):
                continue
            if audio_bytes is not None and (
                capability.max_audio_bytes is not None
                and audio_bytes > capability.max_audio_bytes
            ):
                continue
            matches.append(capability)
        return tuple(sorted(matches, key=lambda item: (
            item.provider, item.model, item.model_version,
        )))
