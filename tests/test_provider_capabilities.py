"""Deterministic provider capability registry tests."""

import unittest

from voxpipe.speech.capabilities import ProviderCapability, ProviderCapabilityRegistry


class ProviderCapabilityRegistryTests(unittest.TestCase):
    def capability(self, **overrides):
        values = {
            "provider": "local",
            "model": "whisper-small",
            "model_version": "2026-01",
            "supported_languages": frozenset({"fa", "en"}),
            "features": frozenset({"word_timestamps", "language_identification"}),
            "local": True,
            "max_audio_duration_ms": 3_600_000,
            "max_audio_bytes": 100_000_000,
            "estimated_cost_per_minute_usd": 0.0,
        }
        values.update(overrides)
        return ProviderCapability(**values)

    def test_register_lookup_and_duplicate_rejection(self):
        capability = self.capability()
        registry = ProviderCapabilityRegistry([capability])
        self.assertEqual(registry.get("local", "whisper-small", "2026-01"), capability)
        with self.assertRaises(ValueError):
            registry.register(capability)

    def test_filters_language_features_locality_and_limits(self):
        local = self.capability()
        remote = self.capability(
            provider="remote", model="hosted-model", model_version="v2",
            local=False, estimated_cost_per_minute_usd=0.02,
            features=frozenset({"word_timestamps", "diarization"}),
        )
        registry = ProviderCapabilityRegistry([remote, local])
        selected = registry.find(
            language="fa", required_features={"word_timestamps"},
            require_local=True, max_cost_per_minute_usd=0.01,
            audio_duration_ms=120_000, audio_bytes=10_000,
        )
        self.assertEqual(selected, (local,))
        self.assertEqual(
            registry.find(language="fa", required_features={"diarization"}),
            (remote,),
        )

    def test_unknown_cost_does_not_pass_cost_ceiling(self):
        unknown = self.capability(estimated_cost_per_minute_usd=None)
        registry = ProviderCapabilityRegistry([unknown])
        self.assertEqual(registry.find(max_cost_per_minute_usd=1.0), ())

    def test_rejects_invalid_cost_and_audio_limits(self):
        with self.assertRaises(ValueError):
            self.capability(estimated_cost_per_minute_usd=float("nan"))
        with self.assertRaises(ValueError):
            self.capability(max_audio_bytes=0)
        with self.assertRaises(ValueError):
            self.capability(local="yes")


if __name__ == "__main__":
    unittest.main()
