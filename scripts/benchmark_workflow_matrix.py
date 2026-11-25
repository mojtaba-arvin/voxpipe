#!/usr/bin/env python3
"""Run a bounded, repeatable PostgreSQL workflow-journal benchmark matrix.

This measures synthetic no-op journal overhead only. Use a dedicated disposable
PostgreSQL database; every run creates a fresh tenant and retains its rows.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
from datetime import datetime, timezone
from typing import Any

from benchmark_workflows import run_benchmark


def parse_worker_counts(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part.strip()) for part in raw.split(","))
    except (TypeError, ValueError):
        raise ValueError("--workers must be a comma-separated list of integers") from None
    if not values or any(not 1 <= value <= 128 for value in values):
        raise ValueError("--workers values must be between 1 and 128")
    if len(set(values)) != len(values):
        raise ValueError("--workers must not contain duplicates")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=int, default=1000)
    parser.add_argument("--workers", type=parse_worker_counts, default=(1, 4, 8, 16))
    parser.add_argument("--repetitions", type=int, default=3,
                        help="measured repetitions per worker count, after one warm-up")
    parser.add_argument("--max-total-workflows", type=int, default=100_000,
                        help="hard cap including warm-up workloads and all repetitions")
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--scan-limit", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--poll-interval-ms", type=float, default=10.0)
    parser.add_argument("--host-label", default="")
    parser.add_argument("--storage-class", default="")
    parser.add_argument("--network-topology", default="")
    parser.add_argument("--confirm-disposable", action="store_true",
                        help="required acknowledgement that the DSN points to a disposable database")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    bounds = (
        ("workflows", args.workflows, 1, 100_000),
        ("repetitions", args.repetitions, 1, 10),
        ("max-total-workflows", args.max_total_workflows, 1, 500_000),
        ("lease-seconds", args.lease_seconds, 3, 3600),
        ("scan-limit", args.scan_limit, 1, 1000),
        ("timeout-seconds", args.timeout_seconds, 1, 86_400),
        ("poll-interval-ms", args.poll_interval_ms, 1, 10_000),
    )
    for name, value, minimum, maximum in bounds:
        if isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError(f"--{name} must be between {minimum} and {maximum}")
    if not args.workers or len(set(args.workers)) != len(args.workers):
        raise ValueError("--workers must contain unique worker counts")
    if any(not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 128
           for value in args.workers):
        raise ValueError("--workers values must be between 1 and 128")
    if not args.confirm_disposable:
        raise ValueError("pass --confirm-disposable only after verifying the DSN is disposable")
    total = len(args.workers) * (args.repetitions + 1) * args.workflows
    if total > args.max_total_workflows:
        raise ValueError(
            f"matrix creates {total} workflows, exceeding --max-total-workflows "
            f"({args.max_total_workflows})"
        )


def _memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def collect_metadata(dsn: str, args: argparse.Namespace) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(dsn) as connection:
        version, max_connections, shared_buffers = connection.execute(
            "SELECT version(), current_setting('max_connections'), "
            "current_setting('shared_buffers')"
        ).fetchone()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "label": args.host_label or None,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "cpu_count": os.cpu_count(),
            "memory_bytes": _memory_bytes(),
        },
        "postgres": {
            "version": version,
            "max_connections": max_connections,
            "shared_buffers": shared_buffers,
        },
        "operator_supplied": {
            "storage_class": args.storage_class or None,
            "network_topology": args.network_topology or None,
        },
        "dsn": "redacted",
    }


def _benchmark_args(args: argparse.Namespace, workers: int) -> argparse.Namespace:
    return argparse.Namespace(
        workflows=args.workflows,
        workers=workers,
        lease_seconds=args.lease_seconds,
        scan_limit=args.scan_limit,
        timeout_seconds=args.timeout_seconds,
        poll_interval_ms=args.poll_interval_ms,
    )


def summarize_runs(runs: list[dict[str, Any]], repetitions: int) -> dict[str, Any]:
    measured = [
        entry["result"] for entry in runs
        if entry["kind"] == "measurement" and entry["result"].get("complete")
    ]
    throughputs = [item["throughput_workflows_per_second"] for item in measured]
    queue_p95 = [item["queue_wait_ms"]["p95"] for item in measured
                 if item["queue_wait_ms"]["p95"] is not None]
    completion_p95 = [item["claim_to_complete_ms"]["p95"] for item in measured
                      if item["claim_to_complete_ms"]["p95"] is not None]
    return {
        "successful_repetitions": len(measured),
        "required_repetitions": repetitions,
        "complete": len(measured) == repetitions,
        "throughput_median_workflows_per_second": (
            round(statistics.median(throughputs), 3) if throughputs else None
        ),
        "throughput_min_workflows_per_second": round(min(throughputs), 3) if throughputs else None,
        "throughput_max_workflows_per_second": round(max(throughputs), 3) if throughputs else None,
        "queue_wait_p95_median_ms": (
            round(statistics.median(queue_p95), 3) if queue_p95 else None
        ),
        "claim_to_complete_p95_median_ms": (
            round(statistics.median(completion_p95), 3) if completion_p95 else None
        ),
    }


def run_matrix(args: argparse.Namespace, dsn: str) -> dict[str, Any]:
    validate_args(args)
    metadata = collect_metadata(dsn, args)
    raw_runs: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    all_complete = True

    for workers in args.workers:
        config_runs: list[dict[str, Any]] = []
        for kind, repetition in [("warmup", 0)] + [
            ("measurement", index) for index in range(1, args.repetitions + 1)
        ]:
            try:
                result = run_benchmark(_benchmark_args(args, workers), dsn)
                entry = {
                    "workers": workers,
                    "kind": kind,
                    "repetition": repetition,
                    "result": result,
                }
            except Exception as exc:
                entry = {
                    "workers": workers,
                    "kind": kind,
                    "repetition": repetition,
                    "result": {"complete": False, "error_type": type(exc).__name__},
                }
            raw_runs.append(entry)
            config_runs.append(entry)
            if not entry["result"].get("complete"):
                all_complete = False
                break
        summaries[str(workers)] = summarize_runs(config_runs, args.repetitions)
        if not summaries[str(workers)]["complete"]:
            all_complete = False
            break

    return {
        "benchmark": "postgres-workflow-journal-matrix-v1",
        "complete": all_complete and len(summaries) == len(args.workers),
        "workload": {
            "workflows_per_run": args.workflows,
            "worker_counts": list(args.workers),
            "measured_repetitions": args.repetitions,
            "warmups_per_worker_count": 1,
            "total_workflow_budget": len(args.workers) * (args.repetitions + 1) * args.workflows,
            "lease_seconds": args.lease_seconds,
            "scan_limit": args.scan_limit,
            "timeout_seconds": args.timeout_seconds,
            "poll_interval_ms": args.poll_interval_ms,
        },
        "metadata": metadata,
        "summary_by_worker_count": summaries,
        "runs": raw_runs,
        "limitations": [
            "Synthetic no-op steps measure journal/claim-loop overhead, not STT or end-to-end throughput.",
            "All generated workflow rows are retained; use only a dedicated disposable database.",
            "Capacity results apply only to the recorded host, PostgreSQL configuration and workload.",
            "A complete matrix is not a target-host systemd, backup-retention or production SLO acceptance test.",
        ],
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    dsn = os.environ.get("VOXPIPE_WORKFLOW_BENCHMARK_DSN", "").strip()
    if not dsn:
        print("Set VOXPIPE_WORKFLOW_BENCHMARK_DSN to a dedicated disposable PostgreSQL database.",
              file=sys.stderr)
        return 2
    try:
        result = run_matrix(args, dsn)
    except Exception as exc:
        print(f"benchmark matrix failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
