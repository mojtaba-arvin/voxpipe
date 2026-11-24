import unittest
from unittest.mock import patch

from voxpipe.speech.circuit_breaker import ProviderCircuitBreaker
from voxpipe.speech.postgres_circuit_breaker import PostgresProviderCircuitBreaker
from voxpipe.speech.runtime import build_speech_resilience_runtime


class FakeSpeechAdapter:
    provider_name = "local-test-provider"

    def __init__(self):
        self.calls = 0

    def transcribe(self, request):
        self.calls += 1
        raise AssertionError("factory tests must not dispatch inference")


class SpeechResilienceRuntimeTests(unittest.TestCase):
    def test_defaults_to_process_local_breaker_without_opening_database(self):
        runtime = build_speech_resilience_runtime(FakeSpeechAdapter(), environ={})
        self.assertEqual(runtime.backend, "process")
        self.assertIsInstance(runtime.breaker, ProviderCircuitBreaker)
        self.assertEqual(runtime.speech_port.provider_name, "local-test-provider")

    def test_process_backend_accepts_explicit_threshold_and_cooldown(self):
        runtime = build_speech_resilience_runtime(FakeSpeechAdapter(), environ={
            "VOXPIPE_CIRCUIT_BREAKER_BACKEND": "process",
            "VOXPIPE_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "5",
            "VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS": "45.5",
        })
        self.assertEqual(runtime.breaker.failure_threshold, 5)
        self.assertEqual(runtime.breaker.cooldown_seconds, 45.5)

    def test_postgres_backend_is_selected_explicitly(self):
        runtime = build_speech_resilience_runtime(FakeSpeechAdapter(), environ={
            "VOXPIPE_CIRCUIT_BREAKER_BACKEND": "postgres",
            "VOXPIPE_DATABASE_DSN": "postgresql://voxpipe_app:secret@db/voxpipe",
            "VOXPIPE_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "4",
            "VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS": "60",
        })
        self.assertEqual(runtime.backend, "postgres")
        self.assertIsInstance(runtime.breaker, PostgresProviderCircuitBreaker)
        self.assertEqual(runtime.breaker.failure_threshold, 4)
        self.assertEqual(runtime.breaker.cooldown_seconds, 60.0)

    def test_postgres_selection_without_dsn_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "VOXPIPE_DATABASE_DSN"):
            build_speech_resilience_runtime(FakeSpeechAdapter(), environ={
                "VOXPIPE_CIRCUIT_BREAKER_BACKEND": "postgres",
            })


    def test_postgres_outage_fails_closed_before_provider_dispatch(self):
        adapter = FakeSpeechAdapter()
        runtime = build_speech_resilience_runtime(adapter, environ={
            "VOXPIPE_CIRCUIT_BREAKER_BACKEND": "postgres",
            "VOXPIPE_DATABASE_DSN": "postgresql://voxpipe_app:secret@db/voxpipe",
        })
        with patch("psycopg.connect", side_effect=ConnectionError("database unavailable")):
            with self.assertRaisesRegex(ConnectionError, "database unavailable"):
                runtime.speech_port.transcribe(object())
        self.assertEqual(adapter.calls, 0)

    def test_unknown_backend_and_malformed_limits_are_rejected(self):
        invalid = (
            {"VOXPIPE_CIRCUIT_BREAKER_BACKEND": "redis"},
            {"VOXPIPE_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "0"},
            {"VOXPIPE_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "3.5"},
            {"VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS": "nan"},
            {"VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS": "3601"},
        )
        for environ in invalid:
            with self.subTest(environ=environ), self.assertRaises(ValueError):
                build_speech_resilience_runtime(FakeSpeechAdapter(), environ=environ)


if __name__ == "__main__":
    unittest.main()
