import unittest

from voxpipe.speech.circuit_breaker import CircuitPermit, ProviderCircuitBreaker


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class ProviderCircuitBreakerTests(unittest.TestCase):
    def test_opens_after_consecutive_failures_and_closes_after_successful_probe(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=clock)
        first = breaker.try_acquire("provider-a")
        self.assertIsInstance(first, CircuitPermit)
        self.assertFalse(first.is_probe)
        breaker.record_failure(first)
        self.assertEqual(breaker.open_providers(), frozenset())
        second = breaker.try_acquire("provider-a")
        self.assertIsInstance(second, CircuitPermit)
        breaker.record_failure(second)
        self.assertEqual(breaker.open_providers(), frozenset({"provider-a"}))
        self.assertIsNone(breaker.try_acquire("provider-a"))
        clock.value = 10
        probe = breaker.try_acquire("provider-a")
        self.assertTrue(probe.is_probe)
        self.assertEqual(breaker.open_providers(), frozenset({"provider-a"}))
        self.assertIsNone(breaker.try_acquire("provider-a"))
        self.assertTrue(breaker.record_success(probe))
        self.assertEqual(breaker.open_providers(), frozenset())
        self.assertFalse(breaker.try_acquire("provider-a").is_probe)

    def test_failed_half_open_probe_reopens_and_restarts_cooldown(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
        breaker.record_failure(breaker.try_acquire("provider-a"))
        clock.value = 5
        probe = breaker.try_acquire("provider-a")
        self.assertTrue(probe.is_probe)
        self.assertTrue(breaker.record_failure(probe))
        self.assertIn("provider-a", breaker.open_providers())
        clock.value = 9
        self.assertIsNone(breaker.try_acquire("provider-a"))
        clock.value = 10
        self.assertTrue(breaker.try_acquire("provider-a").is_probe)

    def test_cancelled_probe_can_be_released_and_old_permit_is_fenced(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
        breaker.record_failure(breaker.try_acquire("provider-a"))
        clock.value = 5
        probe = breaker.try_acquire("provider-a")
        self.assertTrue(probe.is_probe)
        self.assertTrue(breaker.release_probe(probe))
        self.assertFalse(breaker.record_success(probe))
        self.assertNotIn("provider-a", breaker.open_providers())
        new_probe = breaker.try_acquire("provider-a")
        self.assertTrue(new_probe.is_probe)

    def test_stale_closed_request_cannot_close_circuit_after_other_failures_open_it(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=5, clock=clock)
        first = breaker.try_acquire("provider-a")
        second = breaker.try_acquire("provider-a")
        breaker.record_failure(first)
        breaker.record_failure(second)
        self.assertIn("provider-a", breaker.open_providers())
        self.assertFalse(breaker.record_success(first))
        self.assertIn("provider-a", breaker.open_providers())

    def test_open_provider_snapshot_contains_only_operational_metadata(self):
        clock = FakeClock()
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
        breaker.record_failure(breaker.try_acquire("provider-a"))
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
            breaker.try_acquire("")
        with self.assertRaises(ValueError):
            breaker.try_acquire("provider-a", now=float("inf"))
        with self.assertRaises(TypeError):
            breaker.record_success("provider-a")


if __name__ == "__main__":
    unittest.main()
