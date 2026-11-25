"""Durable ingestion worker with lease fencing, supervision, and metrics."""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Any
from uuid import UUID

from voxpipe.ingestion.jobs import JobLeaseLost, PostgresIngestionJobQueue, WorkerHeartbeatMissing
from voxpipe.ingestion.service import IngestionFailed, IngestionInProgress, IngestionService


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    job_id: str | None
    outcome: str
    error_code: str | None = None


class WorkerMetrics:
    """In-process counters and durations, partitioned by authorized tenant."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._durations: dict[str, tuple[int, float]] = {}

    def record(self, tenant_id: str, outcome: str, duration_seconds: float) -> None:
        with self._lock:
            self._counts[(tenant_id, outcome)] += 1
            count, total = self._durations.get(tenant_id, (0, 0.0))
            self._durations[tenant_id] = (count + 1, total + max(0.0, duration_seconds))


    def aggregate_snapshot(self) -> dict[str, Any]:
        """Return low-cardinality operational metrics without tenant identifiers."""
        with self._lock:
            outcomes: dict[str, int] = defaultdict(int)
            for (_, outcome), count in self._counts.items():
                outcomes[outcome] += count
            execution_count = sum(count for count, _ in self._durations.values())
            duration_total = sum(total for _, total in self._durations.values())
            return {
                "outcomes": dict(sorted(outcomes.items())),
                "execution_count": execution_count,
                "processing_duration_seconds_total": round(duration_total, 3),
            }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            tenants = {tenant for tenant, _ in self._counts}
            return {
                tenant: {
                    "outcomes": {
                        outcome: count
                        for (candidate, outcome), count in self._counts.items()
                        if candidate == tenant
                    },
                    "execution_count": self._durations.get(tenant, (0, 0.0))[0],
                    "processing_duration_seconds_total": round(
                        self._durations.get(tenant, (0, 0.0))[1], 3
                    ),
                }
                for tenant in sorted(tenants)
            }


class IngestionJobWorker:
    """Execute one tenant-scoped job per call and fence lost leases.

    A failed heartbeat is tolerated only while the last confirmed lease renewal
    still leaves a safety margin. Once that margin is exhausted, processing
    results are never acknowledged, even if the database later recovers.
    """

    def __init__(
        self,
        *,
        queue: PostgresIngestionJobQueue,
        service: IngestionService,
        worker_id: str,
        lease_seconds: int = 60,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 300,
    ) -> None:
        try:
            self._worker_id = str(UUID(worker_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("worker_id must be a UUID") from None
        if lease_seconds < 3:
            raise ValueError("lease_seconds must be at least 3 seconds for heartbeat safety")
        if retry_base_seconds < 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("retry delay bounds are invalid")
        self._queue = queue
        self._service = service
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self.metrics = WorkerMetrics()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def register_worker(self, tenant_id: str) -> None:
        self._queue.register_worker(tenant_id, self._worker_id)

    def heartbeat_worker(self, tenant_id: str) -> None:
        self._queue.heartbeat_worker(tenant_id, self._worker_id)

    def stop_worker(self, tenant_id: str) -> None:
        self._queue.stop_worker(tenant_id, self._worker_id)

    def _set_worker_state(self, tenant_id: str, state: str) -> None:
        try:
            self._queue.set_worker_state(tenant_id, self._worker_id, state)
        except Exception:
            logging.getLogger("voxpipe.worker").warning("worker state update failed")

    def run_once(self, tenant_id: str) -> JobExecutionResult:
        try:
            tenant = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id must be a UUID from the trusted scheduler") from None

        job = self._queue.claim_next(
            tenant, self._worker_id, lease_seconds=self._lease_seconds
        )
        if job is None:
            return JobExecutionResult(None, "idle")
        self._set_worker_state(tenant, "busy")
        try:
            return self._execute_claimed_job(tenant, job)
        finally:
            self._set_worker_state(tenant, "idle")

    def _execute_claimed_job(self, tenant: str, job: dict[str, Any]) -> JobExecutionResult:
        started = time.monotonic()
        job_id = str(job["job_id"])
        session_id = str(job["session_id"])
        stop = Event()
        lease_lost = Event()
        heartbeat = Thread(
            target=self._heartbeat_loop,
            args=(tenant, job_id, stop, lease_lost),
            name=f"voxpipe-job-heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat.start()

        try:
            self._service.resume_session(tenant, session_id)
        except IngestionInProgress:
            if lease_lost.is_set():
                result = JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            else:
                result = self._record_failure(tenant, job, "INGESTION_IN_PROGRESS", retryable=True)
        except IngestionFailed as error:
            if lease_lost.is_set():
                result = JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            else:
                retryable = bool(
                    error.session
                    and error.session.get("failure", {}).get("retryable", False)
                )
                result = self._record_failure(tenant, job, error.code, retryable=retryable)
        except Exception:
            if lease_lost.is_set():
                result = JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            else:
                result = self._record_failure(
                    tenant, job, "INGESTION_WORKER_UNEXPECTED", retryable=False
                )
        else:
            result = None
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, self._lease_seconds / 3))

        if result is None:
            if lease_lost.is_set():
                result = JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            else:
                try:
                    self._queue.complete(tenant, job_id, self._worker_id)
                    result = JobExecutionResult(job_id, "completed")
                except JobLeaseLost:
                    result = JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")

        self.metrics.record(tenant, result.outcome, time.monotonic() - started)
        return result

    def _record_failure(
        self,
        tenant_id: str,
        job: dict[str, Any],
        error_code: str,
        *,
        retryable: bool,
    ) -> JobExecutionResult:
        attempt = int(job["attempts"])
        delay = min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** max(0, attempt - 1)),
        )
        try:
            failed = self._queue.fail(
                tenant_id,
                str(job["job_id"]),
                self._worker_id,
                error_code,
                retryable=retryable,
                retry_delay_seconds=delay,
            )
        except JobLeaseLost:
            return JobExecutionResult(
                str(job["job_id"]), "lease_lost", "INGESTION_JOB_LEASE_LOST"
            )
        return JobExecutionResult(
            str(job["job_id"]),
            "retry_scheduled" if failed["state"] == "pending" else "dead",
            error_code,
        )

    def _heartbeat_loop(
        self,
        tenant_id: str,
        job_id: str,
        stop: Event,
        lease_lost: Event,
    ) -> None:
        interval = self._lease_seconds / 3
        last_confirmed = time.monotonic()
        safety_window = self._lease_seconds * 0.75
        while not stop.wait(interval):
            try:
                self._queue.heartbeat(
                    tenant_id,
                    job_id,
                    self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
                last_confirmed = time.monotonic()
            except JobLeaseLost:
                lease_lost.set()
                return
            except Exception:
                if time.monotonic() - last_confirmed >= safety_window:
                    lease_lost.set()
                    return


def _worker_heartbeat_loop(
    worker: IngestionJobWorker,
    tenant_ids: list[str],
    registered: set[str],
    stop_event: Event,
    interval_seconds: float,
    logger: logging.Logger,
) -> None:
    """Refresh process liveness independently of job execution and lease renewal."""
    try:
        while not stop_event.is_set():
            for tenant_id in tenant_ids:
                if stop_event.is_set():
                    break
                try:
                    if tenant_id not in registered:
                        worker.register_worker(tenant_id)
                        registered.add(tenant_id)
                    else:
                        worker.heartbeat_worker(tenant_id)
                except WorkerHeartbeatMissing:
                    registered.discard(tenant_id)
                except Exception:
                    # Never log driver exception strings, DSNs, or tenant identifiers.
                    logger.warning("worker liveness heartbeat update failed; retrying")
            if stop_event.wait(interval_seconds):
                break
    finally:
        for tenant_id in tuple(registered):
            try:
                worker.stop_worker(tenant_id)
            except Exception:
                logger.warning("worker shutdown heartbeat update failed")


def run_forever(
    worker: IngestionJobWorker,
    tenant_ids: list[str],
    stop_event: Event,
    *,
    poll_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
    heartbeat_interval_seconds: float = 15.0,
    logger: logging.Logger | None = None,
) -> None:
    """Supervise tenant polling and emit independent process liveness heartbeats."""
    if (
        not tenant_ids
        or poll_seconds <= 0
        or max_backoff_seconds <= 0
        or heartbeat_interval_seconds <= 0
    ):
        raise ValueError("tenant allowlist and positive polling, backoff, and heartbeat limits are required")
    log = logger or logging.getLogger("voxpipe.worker")
    registered: set[str] = set()
    if not stop_event.is_set():
        for tenant_id in tenant_ids:
            try:
                worker.register_worker(tenant_id)
                registered.add(tenant_id)
            except Exception:
                log.warning("worker registration failed; retrying in heartbeat loop")
    heartbeat_thread = Thread(
        target=_worker_heartbeat_loop,
        args=(worker, tenant_ids, registered, stop_event, heartbeat_interval_seconds, log),
        name=f"voxpipe-worker-liveness-{worker.worker_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    failure_streak = 0
    try:
        while not stop_event.is_set():
            did_work = False
            try:
                for tenant_id in tenant_ids:
                    if stop_event.is_set():
                        break
                    result = worker.run_once(tenant_id)
                    if result.outcome != "idle":
                        did_work = True
                        log.info(
                            "job execution job_id=%s outcome=%s error_code=%s",
                            result.job_id,
                            result.outcome,
                            result.error_code or "-",
                        )
                failure_streak = 0
            except Exception:
                failure_streak += 1
                ceiling = min(max_backoff_seconds, poll_seconds * (2 ** min(failure_streak - 1, 10)))
                delay = random.uniform(ceiling * 0.8, ceiling)
                # Driver exception strings may contain DSNs or connection metadata.
                log.error("worker queue operation failed; retrying with bounded backoff")
                stop_event.wait(delay)
                continue

            if not did_work and not stop_event.is_set():
                stop_event.wait(poll_seconds)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=max(1.0, heartbeat_interval_seconds * 2))
