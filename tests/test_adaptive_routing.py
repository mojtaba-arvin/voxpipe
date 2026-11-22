import unittest
from datetime import datetime, timedelta, timezone

from voxpipe.speech.capabilities import ProviderCapability, ProviderCapabilityRegistry
from voxpipe.speech.routing import (AdaptiveModelRouter, ModelQualityEvidence, NoEligibleModel, RoutingPolicy)

NOW = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)


def capability(provider, model, version, *, local=True, cost=0.0, languages=frozenset({"fa-IR"}), features=frozenset({"transcription"})):
    return ProviderCapability(provider, model, version, languages, features, local,
        max_audio_duration_ms=600_000, max_audio_bytes=10_000_000,
        estimated_cost_per_minute_usd=cost)


def evidence(provider, model, version, *, quality=.9, latency=100, available=True, age=60):
    return ModelQualityEvidence("transcription", provider, model, version, quality, latency,
        available, NOW - timedelta(seconds=age), "benchmark-run-123")


class AdaptiveRoutingTests(unittest.TestCase):
    def setUp(self):
        self.local_a = capability("local", "whisper-small", "v1")
        self.local_b = capability("local", "whisper-medium", "v2")
        self.registry = ProviderCapabilityRegistry([self.local_a, self.local_b])
        self.evidence = [
            evidence("local", "whisper-small", "v1", quality=.91, latency=120),
            evidence("local", "whisper-medium", "v2", quality=.94, latency=180),
        ]
        self.policy = RoutingPolicy("transcription", "policy-v1",
            frozenset({("local", "whisper-small", "v1"), ("local", "whisper-medium", "v2")}),
            quality_floor=.90, max_latency_ms=500, max_cost_per_minute_usd=0.0,
            require_local=True, max_evidence_age_seconds=3600, max_fallbacks=1)

    def test_selects_highest_quality_and_returns_bounded_fallback(self):
        decision = AdaptiveModelRouter(self.registry, self.evidence).route(
            self.policy, language="fa-IR", required_features={"transcription"}, now=NOW)
        self.assertEqual(decision.selected.capability.model, "whisper-medium")
        self.assertEqual(len(decision.fallbacks), 1)
        self.assertEqual(decision.fallbacks[0].capability.model, "whisper-small")

    def test_stale_missing_unavailable_and_low_quality_evidence_fail_closed(self):
        bad = [evidence("local", "whisper-small", "v1", age=7200),
               evidence("local", "whisper-medium", "v2", available=False)]
        with self.assertRaises(NoEligibleModel):
            AdaptiveModelRouter(self.registry, bad).route(self.policy, language="fa-IR", now=NOW)
        low = [evidence("local", "whisper-small", "v1", quality=.2),
               evidence("local", "whisper-medium", "v2", quality=.3)]
        with self.assertRaises(NoEligibleModel):
            AdaptiveModelRouter(self.registry, low).route(self.policy, language="fa-IR", now=NOW)

    def test_allowlist_privacy_cost_and_open_circuit_are_enforced(self):
        remote = capability("remote", "hosted", "v1", local=False, cost=.01)
        registry = ProviderCapabilityRegistry([self.local_a, remote])
        ev = self.evidence + [evidence("remote", "hosted", "v1", quality=.99, latency=10)]
        with self.assertRaises(NoEligibleModel):
            AdaptiveModelRouter(registry, ev).route(self.policy, language="fa-IR", now=NOW,
                open_circuit_providers={"local"})
        remote_policy = RoutingPolicy("transcription", "remote-explicit",
            frozenset({("remote", "hosted", "v1")}), quality_floor=.9, max_latency_ms=500,
            max_cost_per_minute_usd=.02, require_local=False, max_fallbacks=0)
        remote_decision = AdaptiveModelRouter(registry, ev).route(remote_policy, language="fa-IR", now=NOW)
        self.assertEqual(remote_decision.selected.capability.provider, "remote")
        with self.assertRaises(NoEligibleModel):
            AdaptiveModelRouter(registry, ev).route(remote_policy, language="fa-IR", now=NOW,
                open_circuit_providers={"remote"})

    def test_safe_metadata_contains_no_audio_transcript_or_tenant_fields(self):
        decision = AdaptiveModelRouter(self.registry, self.evidence).route(self.policy,
            language="fa-IR", now=NOW)
        metadata = decision.safe_metadata()
        self.assertNotIn("tenant_id", str(metadata))
        self.assertNotIn("transcript", str(metadata))
        self.assertNotIn("audio", str(metadata))
        self.assertEqual(metadata["policy_version"], "policy-v1")

    def test_duplicate_evidence_and_invalid_policy_are_rejected(self):
        with self.assertRaises(ValueError):
            AdaptiveModelRouter(self.registry, [self.evidence[0], self.evidence[0]])
        with self.assertRaises(ValueError):
            RoutingPolicy("task", "v1", frozenset())
        with self.assertRaises(ValueError):
            RoutingPolicy("task", "v1", frozenset({("p", "m", "v")}), max_fallbacks=6)

    def test_future_evidence_and_naive_clock_are_rejected_or_ignored(self):
        future = ModelQualityEvidence("transcription", "local", "whisper-small", "v1", .99,
            50, True, NOW + timedelta(seconds=1), "future")
        with self.assertRaises(NoEligibleModel):
            AdaptiveModelRouter(self.registry, [future]).route(self.policy, language="fa-IR", now=NOW)
        with self.assertRaises(ValueError):
            AdaptiveModelRouter(self.registry, self.evidence).route(self.policy,
                language="fa-IR", now=datetime(2026, 11, 22))


if __name__ == "__main__": unittest.main()
