"""Real PostgreSQL tests for cross-process provider circuit coordination."""
from __future__ import annotations

import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from voxpipe.speech.postgres_circuit_breaker import PostgresProviderCircuitBreaker

ADMIN_DSN = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN")
APP_DSN = os.environ.get(
    "VOXPIPE_TEST_POSTGRES_APP_DSN",
    "postgresql://voxpipe_app:voxpipe_app@localhost:5432/voxpipe_test",
)


@unittest.skipUnless(ADMIN_DSN, "PostgreSQL integration DSN is not configured")
class PostgresProviderCircuitBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        with psycopg.connect(ADMIN_DSN) as connection:
            connection.execute("TRUNCATE TABLE provider_circuit_breakers")

    def test_instances_share_open_state_and_fence_one_half_open_probe(self) -> None:
        first = PostgresProviderCircuitBreaker(APP_DSN, failure_threshold=1, cooldown_seconds=1)
        second = PostgresProviderCircuitBreaker(APP_DSN, failure_threshold=1, cooldown_seconds=1)
        permit = first.try_acquire("speech-provider-a")
        self.assertIsNotNone(permit)
        self.assertTrue(first.record_failure(permit))
        self.assertEqual(second.open_providers(), frozenset({"speech-provider-a"}))
        self.assertIsNone(second.try_acquire("speech-provider-a"))

        time.sleep(1.1)
        contenders = 16
        barrier = Barrier(contenders)

        def acquire(index: int):
            breaker = first if index % 2 else second
            barrier.wait()
            return breaker.try_acquire("speech-provider-a")

        with ThreadPoolExecutor(max_workers=contenders) as pool:
            permits = list(pool.map(acquire, range(contenders)))
        probes = [item for item in permits if item is not None and item.is_probe]
        self.assertEqual(len(probes), 1)
        self.assertEqual(sum(item is not None for item in permits), 1)
        self.assertIn("speech-provider-a", first.open_providers())
        self.assertTrue(second.record_success(probes[0]))
        self.assertEqual(first.open_providers(), frozenset())

    def test_failed_probe_reopens_and_release_allows_a_new_probe(self) -> None:
        breaker = PostgresProviderCircuitBreaker(APP_DSN, failure_threshold=1, cooldown_seconds=1)
        permit = breaker.try_acquire("speech-provider-b")
        self.assertTrue(breaker.record_failure(permit))
        time.sleep(1.1)
        probe = breaker.try_acquire("speech-provider-b")
        self.assertTrue(probe.is_probe)
        self.assertTrue(breaker.record_failure(probe))
        self.assertIn("speech-provider-b", breaker.open_providers())
        time.sleep(1.1)
        probe = breaker.try_acquire("speech-provider-b")
        self.assertTrue(probe.is_probe)
        self.assertTrue(breaker.release_probe(probe))
        self.assertFalse(breaker.record_success(probe))
        self.assertTrue(breaker.try_acquire("speech-provider-b").is_probe)

    def test_stale_closed_permit_cannot_clear_an_open_circuit(self) -> None:
        breaker = PostgresProviderCircuitBreaker(APP_DSN, failure_threshold=1, cooldown_seconds=1)
        stale = breaker.try_acquire("speech-provider-c")
        self.assertTrue(breaker.record_failure(stale))
        self.assertFalse(breaker.record_success(stale))
        self.assertIn("speech-provider-c", breaker.open_providers())

    def test_rejects_invalid_configuration_and_provider(self) -> None:
        for kwargs in ({"failure_threshold": True}, {"failure_threshold": 0},
                       {"cooldown_seconds": float("nan")}, {"cooldown_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PostgresProviderCircuitBreaker(APP_DSN, **kwargs)
        breaker = PostgresProviderCircuitBreaker(APP_DSN)
        with self.assertRaises(ValueError):
            breaker.try_acquire("")
        with self.assertRaises(TypeError):
            breaker.record_success("not-a-permit")


if __name__ == "__main__":
    unittest.main()
