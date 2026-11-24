#!/usr/bin/env python3
"""Measure PostgreSQL workflow-journal throughput with synthetic no-op steps.

This isolates journal/claim-loop overhead, not STT or end-to-end throughput.
Use only a dedicated disposable database; synthetic rows are retained.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from uuid import uuid4

from voxpipe.workflows import PostgresWorkflowRepository, WorkflowStepSpec


def percentile(values: list[float], percentile_value: float) -> float | None:
    """Return a nearest-rank percentile; None when no samples were recorded."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(percentile_value * len(ordered) + 0.999999999))
    return ordered[min(rank, len(ordered)) - 1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--scan-limit", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--poll-interval-ms", type=float, default=10.0)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    bounds = (
        ("workflows", args.workflows, 1, 100_000),
        ("workers", args.workers, 1, 128),
        ("lease-seconds", args.lease_seconds, 3, 3600),
        ("scan-limit", args.scan_limit, 1, 1000),
        ("timeout-seconds", args.timeout_seconds, 1, 86_400),
        ("poll-interval-ms", args.poll_interval_ms, 1, 10_000),
    )
    for name, value, minimum, maximum in bounds:
        if isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError(f"--{name} must be between {minimum} and {maximum}")


def run_benchmark(args: argparse.Namespace, dsn: str) -> dict:
    """Create one synthetic tenant/workload and drain it with N independent workers."""
    validate_args(args)
    run_id, tenant_id = str(uuid4()), str(uuid4())
    repository = PostgresWorkflowRepository(dsn)
    enqueue_times: dict[str, float] = {}

    setup_started = time.monotonic()
    for sequence in range(args.workflows):
        workflow_id = str(uuid4())
        step = WorkflowStepSpec(
            str(uuid4()), "benchmark.noop",
            {"benchmark_run": run_id, "sequence": sequence}, max_attempts=1,
        )
        repository.create_workflow(
            tenant_id, workflow_id, "journal-benchmark", "1.0",
            f"{run_id}:{sequence}", (step,),
        )
        enqueue_times[workflow_id] = time.monotonic()
    setup_seconds = time.monotonic() - setup_started

    barrier = threading.Barrier(args.workers + 1)
    stop_event = threading.Event()
    lock = threading.Lock()
    completed = 0
    errors: Counter[str] = Counter()
    claims: set[tuple[str, str]] = set()
    queue_wait_ms: list[float] = []
    step_duration_ms: list[float] = []
    started_at = 0.0

    def worker(worker_number: int) -> None:
        nonlocal completed
        worker_id = str(uuid4())
        worker_repository = PostgresWorkflowRepository(dsn)
        barrier.wait(timeout=30)
        while not stop_event.is_set():
            if time.monotonic() - started_at >= args.timeout_seconds:
                stop_event.set()
                return
            try:
                claim = worker_repository.claim_next_ready_step(
                    tenant_id, worker_id, lease_seconds=args.lease_seconds,
                    scan_limit=args.scan_limit,
                )
                if claim is None:
                    with lock:
                        if completed >= args.workflows:
                            return
                    stop_event.wait(args.poll_interval_ms / 1000)
                    continue
                workflow_id, step_id = str(claim["workflow_id"]), str(claim["step_id"])
                claimed_at = time.monotonic()
                with lock:
                    key = (workflow_id, step_id)
                    if key in claims:
                        errors["duplicate_claim"] += 1
                    claims.add(key)
                    queue_wait_ms.append((claimed_at - enqueue_times[workflow_id]) * 1000)
                worker_repository.complete_step(
                    tenant_id, workflow_id, step_id, worker_id,
                    {"benchmark_run": run_id, "worker": worker_number},
                )
                finished_at = time.monotonic()
                with lock:
                    completed += 1
                    step_duration_ms.append((finished_at - claimed_at) * 1000)
            except Exception as exc:
                # Exception text can contain DSNs; report only the class.
                with lock:
                    errors[type(exc).__name__] += 1
                stop_event.wait(args.poll_interval_ms / 1000)

    threads = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(args.workers)
    ]
    for thread in threads:
        thread.start()
    started_at = time.monotonic()
    barrier.wait(timeout=30)
    for thread in threads:
        thread.join(timeout=args.timeout_seconds + 30)
    stop_event.set()
    for thread in threads:
        if thread.is_alive():
            errors["worker_join_timeout"] += 1

    elapsed = max(time.monotonic() - started_at, 0.000001)
    return {
        "benchmark": "postgres-workflow-journal-noop-v1",
        "run_id": run_id,
        "tenant_id": tenant_id,
        "workload": {
            "workflows": args.workflows, "steps_per_workflow": 1,
            "workers": args.workers, "lease_seconds": args.lease_seconds,
            "scan_limit": args.scan_limit, "poll_interval_ms": args.poll_interval_ms,
        },
        "setup_seconds": round(setup_seconds, 6),
        "elapsed_seconds": round(elapsed, 6),
        "completed": completed,
        "claimed_unique": len(claims),
        "errors": dict(errors),
        "throughput_workflows_per_second": round(completed / elapsed, 3),
        "queue_wait_ms": {
            "p50": percentile(queue_wait_ms, 0.50),
            "p95": percentile(queue_wait_ms, 0.95),
            "p99": percentile(queue_wait_ms, 0.99),
        },
        "claim_to_complete_ms": {
            "p50": percentile(step_duration_ms, 0.50),
            "p95": percentile(step_duration_ms, 0.95),
            "p99": percentile(step_duration_ms, 0.99),
        },
        "complete": completed == args.workflows
        and len(claims) == args.workflows and not errors,
        "limitations": [
            "Synthetic no-op steps measure journal/claim-loop overhead, not STT or end-to-end throughput.",
            "Run only against a disposable PostgreSQL database; synthetic rows are retained.",
            "Record PostgreSQL version, host CPU/RAM, storage, connection limits and network topology.",
            "Repeat worker/workload configurations; do not infer production capacity from one run.",
        ],
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    dsn = os.environ.get("VOXPIPE_WORKFLOW_BENCHMARK_DSN")
    if not dsn:
        print("Set VOXPIPE_WORKFLOW_BENCHMARK_DSN to a dedicated disposable PostgreSQL database.",
              file=sys.stderr)
        return 2
    try:
        result = run_benchmark(args, dsn)
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
