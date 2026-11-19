"""Unit tests for durable ingestion job worker orchestration."""

from __future__ import annotations

import unittest
from uuid import uuid4

from voxpipe.ingestion.jobs import JobLeaseLost
from voxpipe.ingestion.service import IngestionFailed, IngestionInProgress
from voxpipe.ingestion.worker import IngestionJobWorker


class FakeQueue:
    def __init__(self, *, state: str = "leased", lose_on_complete: bool = False) -> None:
        self.job = {
            "job_id": str(uuid4()),
            "session_id": str(uuid4()),
            "attempts": 2,
            "max_attempts": 3,
            "state": state,
        }
        self.completed = False
        self.failure = None
        self.heartbeats = 0
        self.lose_on_complete = lose_on_complete

    def claim_next(self, tenant_id: str, worker_id: str, *, lease_seconds: int) -> dict:
        return self.job

    def heartbeat(self, tenant_id: str, job_id: str, worker_id: str, *, lease_seconds: int) -> dict:
        self.heartbeats += 1
        return self.job

    def complete(self, tenant_id: str, job_id: str, worker_id: str) -> dict:
        if self.lose_on_complete:
            raise JobLeaseLost("simulated stale lease")
        self.completed = True
        return {**self.job, "state": "completed"}

    def fail(
        self, tenant_id: str, job_id: str, worker_id: str, error_code: str,
        *, retryable: bool, retry_delay_seconds: int,
    ) -> dict:
        self.failure = {
            "error_code": error_code,
            "retryable": retryable,
            "retry_delay_seconds": retry_delay_seconds,
        }
        return {**self.job, "state": "pending" if retryable else "dead"}


class FakeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def resume_session(self, tenant_id: str, session_id: str) -> None:
        self.calls.append((tenant_id, session_id))
        if self.error:
            raise self.error


class IngestionJobWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant_id = str(uuid4())
        self.worker_id = str(uuid4())

    def worker(self, queue: FakeQueue, service: FakeService) -> IngestionJobWorker:
        return IngestionJobWorker(
            queue=queue,
            service=service,
            worker_id=self.worker_id,
            lease_seconds=3,
            retry_base_seconds=4,
            retry_max_seconds=30,
        )

    def test_success_completes_the_claimed_job(self) -> None:
        queue = FakeQueue()
        service = FakeService()
        result = self.worker(queue, service).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "completed")
        self.assertTrue(queue.completed)
        self.assertEqual(service.calls, [(self.tenant_id, queue.job["session_id"])])

    def test_live_session_lease_is_retried_with_bounded_backoff(self) -> None:
        queue = FakeQueue()
        service = FakeService(IngestionInProgress(queue.job["session_id"]))
        result = self.worker(queue, service).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "retry_scheduled")
        self.assertEqual(queue.failure["error_code"], "INGESTION_IN_PROGRESS")
        self.assertTrue(queue.failure["retryable"])
        self.assertEqual(queue.failure["retry_delay_seconds"], 8)

    def test_permanent_session_failure_is_dead_lettered(self) -> None:
        queue = FakeQueue()
        service = FakeService(
            IngestionFailed(
                "INGESTION_INVALID_MEDIA",
                {"failure": {"retryable": False}},
            )
        )
        result = self.worker(queue, service).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "dead")
        self.assertFalse(queue.failure["retryable"])

    def test_stale_worker_does_not_acknowledge_job(self) -> None:
        queue = FakeQueue(lose_on_complete=True)
        service = FakeService()
        result = self.worker(queue, service).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "lease_lost")
        self.assertFalse(queue.completed)

    def test_invalid_tenant_and_worker_ids_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            IngestionJobWorker(
                queue=FakeQueue(),
                service=FakeService(),
                worker_id="not-a-uuid",
            )
        with self.assertRaises(ValueError):
            self.worker(FakeQueue(), FakeService()).run_once("not-a-tenant")


if __name__ == "__main__":
    unittest.main()
