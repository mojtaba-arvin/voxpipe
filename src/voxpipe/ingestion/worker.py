"""Durable ingestion job worker with lease renewal and bounded retries."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Any
from uuid import UUID

from voxpipe.ingestion.jobs import JobLeaseLost, PostgresIngestionJobQueue
from voxpipe.ingestion.service import IngestionFailed, IngestionInProgress, IngestionService


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    job_id: str | None
    outcome: str
    error_code: str | None = None


class IngestionJobWorker:
    """Execute at most one tenant-scoped durable ingestion job per call.

    The heartbeat runs independently while media probing/decoding/storage is
    blocking. Losing the queue lease fences acknowledgements; the session's
    own lease and idempotent asset writes protect the processing boundary.
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
                return JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            return self._record_failure(
                tenant, job, "INGESTION_IN_PROGRESS", retryable=True
            )
        except IngestionFailed as error:
            if lease_lost.is_set():
                return JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            retryable = bool(
                error.session
                and error.session.get("failure", {}).get("retryable", False)
            )
            return self._record_failure(
                tenant, job, error.code, retryable=retryable
            )
        except Exception:
            if lease_lost.is_set():
                return JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
            return self._record_failure(
                tenant, job, "INGESTION_WORKER_UNEXPECTED", retryable=False
            )
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, self._lease_seconds / 3))

        if lease_lost.is_set():
            return JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
        try:
            self._queue.complete(tenant, job_id, self._worker_id)
        except JobLeaseLost:
            return JobExecutionResult(job_id, "lease_lost", "INGESTION_JOB_LEASE_LOST")
        return JobExecutionResult(job_id, "completed")

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
        while not stop.wait(interval):
            try:
                self._queue.heartbeat(
                    tenant_id,
                    job_id,
                    self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
            except JobLeaseLost:
                lease_lost.set()
                return
            except Exception:
                # A transient heartbeat transport failure is not proof that
                # the lease is lost. The next heartbeat may still renew it.
                continue
