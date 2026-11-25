"""Tests for the bounded PostgreSQL workflow benchmark matrix runner."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.benchmark_workflow_matrix import (
    build_parser,
    parse_worker_counts,
    run_matrix,
    summarize_runs,
    validate_args,
)


class WorkflowBenchmarkMatrixTests(unittest.TestCase):
    def test_parses_worker_counts_and_preserves_order(self):
        self.assertEqual(parse_worker_counts("1, 4,8,16"), (1, 4, 8, 16))

    def test_rejects_duplicate_or_out_of_range_worker_counts(self):
        for raw in ("1,1", "0,4", "129", "one,4", ""):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_worker_counts(raw)

    def test_requires_explicit_disposable_database_acknowledgement(self):
        args = build_parser().parse_args([])
        with self.assertRaisesRegex(ValueError, "confirm-disposable"):
            validate_args(args)

    def test_enforces_total_synthetic_workflow_budget(self):
        args = build_parser().parse_args([
            "--workflows", "1000",
            "--workers", "1,4,8,16",
            "--repetitions", "3",
            "--max-total-workflows", "10000",
            "--confirm-disposable",
        ])
        with self.assertRaisesRegex(ValueError, "exceeding"):
            validate_args(args)

    def test_summarizes_only_successful_measurement_repetitions(self):
        def result(throughput, queue_p95, complete=True):
            return {
                "complete": complete,
                "throughput_workflows_per_second": throughput,
                "queue_wait_ms": {"p95": queue_p95},
                "claim_to_complete_ms": {"p95": queue_p95 / 2},
            }

        runs = [
            {"kind": "warmup", "result": result(100, 10)},
            {"kind": "measurement", "result": result(80, 8)},
            {"kind": "measurement", "result": result(100, 10)},
            {"kind": "measurement", "result": result(999, 1, complete=False)},
        ]
        summary = summarize_runs(runs, repetitions=3)
        self.assertEqual(summary["successful_repetitions"], 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["throughput_median_workflows_per_second"], 90.0)
        self.assertEqual(summary["throughput_min_workflows_per_second"], 80)
        self.assertEqual(summary["throughput_max_workflows_per_second"], 100)
        self.assertEqual(summary["queue_wait_p95_median_ms"], 9.0)

    def test_matrix_runs_one_warmup_and_requested_measurements_per_configuration(self):
        args = build_parser().parse_args([
            "--workflows", "10",
            "--workers", "1,4",
            "--repetitions", "2",
            "--confirm-disposable",
        ])
        successful = {
            "complete": True,
            "throughput_workflows_per_second": 10.0,
            "queue_wait_ms": {"p95": 2.0},
            "claim_to_complete_ms": {"p95": 1.0},
        }
        with patch("scripts.benchmark_workflow_matrix.collect_metadata", return_value={"host": {}}):
            with patch("scripts.benchmark_workflow_matrix.run_benchmark", return_value=successful) as run:
                matrix = run_matrix(args, "postgresql://redacted")
        self.assertTrue(matrix["complete"])
        self.assertEqual(run.call_count, 6)
        self.assertEqual(len(matrix["runs"]), 6)
        self.assertEqual(matrix["summary_by_worker_count"]["1"]["successful_repetitions"], 2)
        self.assertEqual(matrix["summary_by_worker_count"]["4"]["successful_repetitions"], 2)
        self.assertEqual(matrix["workload"]["total_workflow_budget"], 60)

    def test_matrix_stops_after_first_incomplete_run(self):
        args = build_parser().parse_args([
            "--workflows", "10",
            "--workers", "1,4",
            "--repetitions", "2",
            "--confirm-disposable",
        ])
        failed = {"complete": False, "error_type": "Timeout"}
        with patch("scripts.benchmark_workflow_matrix.collect_metadata", return_value={}):
            with patch("scripts.benchmark_workflow_matrix.run_benchmark", return_value=failed) as run:
                matrix = run_matrix(args, "postgresql://redacted")
        self.assertFalse(matrix["complete"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(matrix["runs"][0]["kind"], "warmup")
        self.assertNotIn("4", matrix["summary_by_worker_count"])


if __name__ == "__main__":
    unittest.main()
