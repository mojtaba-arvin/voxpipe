"""Unit tests for the durable workflow journal benchmark helpers."""
from __future__ import annotations

import unittest

from scripts.benchmark_workflows import build_parser, percentile, validate_args


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


if __name__ == "__main__":
    unittest.main()
