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
    data_handling: frozenset[str] = frozenset()
    deployment_requirements: frozenset[str] = frozenset()
    data_egress_allowed: bool = False
    deprecated: bool = False
    deprecation_reason: str | None = None
    replacement: tuple[str, str, str] | None = None
    rollback_model_version: str | None = None

    def __post_init__(self) -> None:
        for field in ("provider", "model", "model_version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if not isinstance(self.local, bool):
            raise ValueError("local must be boolean")
        if (not isinstance(self.supported_languages, frozenset) or not self.supported_languages
                or any(not isinstance(x, str) or not x.strip() for x in self.supported_languages)):
            raise ValueError("supported_languages must be a non-empty frozenset of language tags")
        if (not isinstance(self.features, frozenset)
                or any(not isinstance(x, str) or not x.strip() for x in self.features)):
            raise ValueError("features must be a frozenset of non-empty identifiers")
        for field in ("max_audio_duration_ms", "max_audio_bytes"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"{field} must be a positive integer or null")
        for field in ("data_handling", "deployment_requirements"):
            value = getattr(self, field)
            if (not isinstance(value, frozenset)
                    or any(not isinstance(x, str) or not x.strip() for x in value)):
                raise ValueError(f"{field} must be a frozenset of non-empty identifiers")
        if not isinstance(self.data_egress_allowed, bool):
            raise ValueError("data_egress_allowed must be boolean")
        if not isinstance(self.deprecated, bool):
            raise ValueError("deprecated must be boolean")
        if not self.local and self.data_egress_allowed and (
            not self.data_handling or not self.deployment_requirements
        ):
            raise ValueError("remote egress requires explicit data_handling and deployment_requirements")
        if self.deprecation_reason is not None and (
            not isinstance(self.deprecation_reason, str)
            or not self.deprecation_reason.strip()
            or len(self.deprecation_reason) > 512
        ):
            raise ValueError("deprecation_reason must be a non-empty bounded string or null")
        if self.deprecated and self.deprecation_reason is None:
            raise ValueError("deprecated capabilities require a deprecation_reason")
        if not self.deprecated and self.deprecation_reason is not None:
            raise ValueError("deprecation_reason is valid only for deprecated capabilities")
        if self.replacement is not None and (
            not isinstance(self.replacement, tuple) or len(self.replacement) != 3
            or any(not isinstance(x, str) or not x.strip() for x in self.replacement)
        ):
            raise ValueError("replacement must be a provider/model/version tuple or null")
        if self.replacement == (self.provider, self.model, self.model_version):
            raise ValueError("replacement cannot point to the same capability")
        if self.rollback_model_version is not None and (
            not isinstance(self.rollback_model_version, str)
            or not self.rollback_model_version.strip()
            or len(self.rollback_model_version) > 256
        ):
            raise ValueError("rollback_model_version must be a non-empty bounded string or null")
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
        include_deprecated: bool = False,
    ) -> tuple[ProviderCapability, ...]:
        """Return deterministic candidates; hosted egress and deprecated models fail closed."""
        if not isinstance(include_deprecated, bool):
            raise ValueError("include_deprecated must be boolean")
        if language is not None and (not isinstance(language, str) or not language.strip()):
            raise ValueError("language must be a non-empty language tag or null")
        if isinstance(required_features, (str, bytes)):
            raise ValueError("required_features must be an iterable of feature identifiers")
        try:
            features = frozenset(required_features)
        except TypeError:
            raise ValueError("required_features must be an iterable of feature identifiers") from None
        if any(not isinstance(feature, str) or not feature.strip() for feature in features):
            raise ValueError("required_features must contain non-empty identifiers")
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
            if capability.deprecated and not include_deprecated:
                continue
            if not capability.local and not capability.data_egress_allowed:
                continue
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
                capability.max_audio_duration_ms is None
                or audio_duration_ms > capability.max_audio_duration_ms
            ):
                continue
            if audio_bytes is not None and (
                capability.max_audio_bytes is None
                or audio_bytes > capability.max_audio_bytes
            ):
                continue
            matches.append(capability)
        return tuple(sorted(matches, key=lambda item: (
            item.provider, item.model, item.model_version,
        )))
