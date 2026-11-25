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
            data_egress_allowed=True,
            data_handling=frozenset({"audio-egress", "transcript-egress"}),
            deployment_requirements=frozenset({"api-key", "network-egress"}),
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

    def test_unknown_audio_limits_do_not_pass_explicit_constraints(self):
        unknown = self.capability(max_audio_duration_ms=None, max_audio_bytes=None)
        registry = ProviderCapabilityRegistry([unknown])
        self.assertEqual(registry.find(audio_duration_ms=60_000), ())
        self.assertEqual(registry.find(audio_bytes=1024), ())

    def test_remote_capabilities_require_explicit_egress_metadata(self):
        with self.assertRaisesRegex(ValueError, "data_egress_allowed"):
            self.capability(local=False, data_egress_allowed="yes")
        with self.assertRaisesRegex(ValueError, "explicit data_handling"):
            self.capability(
                local=False, data_egress_allowed=True,
                deployment_requirements=frozenset({"api-key"}),
            )
        remote = self.capability(
            local=False, data_egress_allowed=True,
            data_handling=frozenset({"audio-egress"}),
            deployment_requirements=frozenset({"api-key"}),
        )
        registry = ProviderCapabilityRegistry([remote])
        self.assertEqual(registry.find(require_local=False), (remote,))

    def test_remote_egress_is_not_discovered_without_operator_metadata(self):
        remote = self.capability(local=False, data_egress_allowed=False)
        registry = ProviderCapabilityRegistry([remote])
        self.assertEqual(registry.find(require_local=False), ())

    def test_deprecated_capabilities_are_hidden_unless_explicitly_requested(self):
        retired = self.capability(
            deprecated=True,
            deprecation_reason="Provider version is retired",
            replacement=("local", "whisper-small", "2026-02"),
            rollback_model_version="2026-01",
        )
        registry = ProviderCapabilityRegistry([retired])
        self.assertEqual(registry.find(), ())
        self.assertEqual(registry.find(include_deprecated=True), (retired,))
        self.assertEqual(retired.replacement, ("local", "whisper-small", "2026-02"))
        self.assertEqual(retired.rollback_model_version, "2026-01")

    def test_rejects_incomplete_deprecation_and_rollback_metadata(self):
        with self.assertRaisesRegex(ValueError, "deprecation_reason"):
            self.capability(deprecated=True)
        with self.assertRaisesRegex(ValueError, "only for deprecated"):
            self.capability(deprecation_reason="retired")
        with self.assertRaisesRegex(ValueError, "replacement"):
            self.capability(replacement=["local", "model", "v2"])
        with self.assertRaisesRegex(ValueError, "same capability"):
            self.capability(replacement=("local", "whisper-small", "2026-01"))
        with self.assertRaisesRegex(ValueError, "rollback_model_version"):
            self.capability(rollback_model_version=" " )

    def test_rejects_non_frozenset_capability_claims(self):
        with self.assertRaisesRegex(ValueError, "supported_languages"):
            self.capability(supported_languages=["fa"])
        with self.assertRaisesRegex(ValueError, "features"):
            self.capability(features="transcription")

    def test_rejects_malformed_discovery_filters(self):
        registry = ProviderCapabilityRegistry([self.capability()])
        with self.assertRaises(ValueError):
            registry.find(language="")
        with self.assertRaises(ValueError):
            registry.find(required_features="word_timestamps")
        with self.assertRaises(ValueError):
            registry.find(required_features=None)

    def test_rejects_invalid_cost_and_audio_limits(self):
        with self.assertRaises(ValueError):
            self.capability(estimated_cost_per_minute_usd=float("nan"))
        with self.assertRaises(ValueError):
            self.capability(max_audio_bytes=0)
        with self.assertRaises(ValueError):
            self.capability(local="yes")


if __name__ == "__main__":
    unittest.main()
