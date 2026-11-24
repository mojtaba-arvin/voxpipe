"""Unit tests for the durable workflow journal benchmark helpers."""
from __future__ import annotations

import os
import unittest

from scripts.benchmark_workflows import build_parser, percentile, run_benchmark, validate_args

ADMIN_DSN = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN")
APP_DSN = os.environ.get(
    "VOXPIPE_TEST_POSTGRES_APP_DSN",
    "postgresql://voxpipe_app:voxpipe_app@localhost:5432/voxpipe_test",
)


class WorkflowBenchmarkHelperTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([4.0, 1.0, 3.0, 2.0], 0.50), 2.0)
        self.assertEqual(percentile([4.0, 1.0, 3.0, 2.0], 0.95), 4.0)

    def test_percentile_returns_none_for_empty_samples(self):
        self.assertIsNone(percentile([], 0.95))

    def test_default_arguments_are_valid(self):
        validate_args(build_parser().parse_args([]))

    def test_rejects_unbounded_workload(self):
        with self.assertRaises(ValueError):
            validate_args(build_parser().parse_args(["--workflows", "100001"]))

    def test_rejects_invalid_worker_count(self):
        with self.assertRaises(ValueError):
            validate_args(build_parser().parse_args(["--workers", "0"]))


@unittest.skipUnless(ADMIN_DSN, "disposable PostgreSQL integration DSN is not configured")
class WorkflowBenchmarkPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from voxpipe.ingestion import apply_migrations
        apply_migrations(ADMIN_DSN)

    def test_benchmark_drains_real_postgres_workflows_without_duplicates(self):
        args = build_parser().parse_args([
            "--workflows", "24",
            "--workers", "4",
            "--lease-seconds", "10",
            "--scan-limit", "24",
            "--timeout-seconds", "30",
            "--poll-interval-ms", "1",
        ])
        result = run_benchmark(args, APP_DSN)
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["completed"], 24)
        self.assertEqual(result["claimed_unique"], 24)
        self.assertEqual(result["errors"], {})
        self.assertGreater(result["throughput_workflows_per_second"], 0)
        self.assertIsNotNone(result["queue_wait_ms"]["p95"])
        self.assertIsNotNone(result["claim_to_complete_ms"]["p95"])


if __name__ == "__main__":
    unittest.main()
