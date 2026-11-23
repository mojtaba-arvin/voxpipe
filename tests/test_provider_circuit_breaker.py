import unittest

from voxpipe.speech.circuit_breaker import ProviderCircuitBreaker


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class ProviderCircuitBreakerTests(unittest.TestCase):
    def test_opens_after_consecutive_failures_and_closes_after_successful_probe(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=clock)
        breaker.record_failure("provider-a")
        self.assertEqual(breaker.open_providers(), frozenset())
        breaker.record_failure("provider-a")
        self.assertEqual(breaker.open_providers(), frozenset({"provider-a"}))
        self.assertFalse(breaker.try_acquire("provider-a"))
        clock.value = 10
        self.assertTrue(breaker.try_acquire("provider-a"))
        self.assertEqual(breaker.open_providers(), frozenset({"provider-a"}))
        self.assertFalse(breaker.try_acquire("provider-a"))
        breaker.record_success("provider-a")
        self.assertEqual(breaker.open_providers(), frozenset())
        self.assertTrue(breaker.try_acquire("provider-a"))

    def test_failed_half_open_probe_reopens_and_restarts_cooldown(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
        breaker.record_failure("provider-a")
        clock.value = 5
        self.assertTrue(breaker.try_acquire("provider-a"))
        breaker.record_failure("provider-a")
        self.assertIn("provider-a", breaker.open_providers())
        clock.value = 9
        self.assertFalse(breaker.try_acquire("provider-a"))
        clock.value = 10
        self.assertTrue(breaker.try_acquire("provider-a"))

    def test_cancelled_probe_can_be_released_without_closing_circuit(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
        breaker.record_failure("provider-a")
        clock.value = 5
        self.assertTrue(breaker.try_acquire("provider-a"))
        breaker.release_probe("provider-a")
        self.assertNotIn("provider-a", breaker.open_providers())
        self.assertTrue(breaker.try_acquire("provider-a"))

    def test_open_provider_snapshot_contains_only_operational_metadata(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
        breaker.record_failure("provider-a")
        snapshot = breaker.snapshot()
        self.assertEqual(snapshot["provider-a"]["consecutive_failures"], 1)
        self.assertIsNotNone(snapshot["provider-a"]["opened_at_monotonic"])
        self.assertNotIn("request", repr(snapshot))
        self.assertNotIn("transcript", repr(snapshot))

    def test_rejects_invalid_configuration_provider_and_clock(self):
        for kwargs in (
            {"failure_threshold": True},
            {"failure_threshold": 0},
            {"cooldown_seconds": float("nan")},
            {"cooldown_seconds": 0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    ProviderCircuitBreaker(**kwargs)
        breaker = ProviderCircuitBreaker(clock=FakeClock())
        with self.assertRaises(ValueError):
            breaker.record_failure("")
        with self.assertRaises(ValueError):
            breaker.try_acquire("provider-a", now=float("inf"))


if __name__ == "__main__":
    unittest.main()
