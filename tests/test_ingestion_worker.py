"""Unit tests for durable ingestion worker orchestration and supervision."""

from __future__ import annotations

import logging
import time
import unittest
from threading import Event, Thread
from uuid import uuid4

from voxpipe.ingestion.jobs import JobLeaseLost
from voxpipe.ingestion.service import IngestionFailed, IngestionInProgress
from voxpipe.ingestion.worker import IngestionJobWorker, run_forever


class FakeQueue:
    def __init__(
        self, *, state: str = "leased", lose_on_complete: bool = False,
        heartbeat_error: Exception | None = None,
    ) -> None:
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
        self.heartbeat_error = heartbeat_error
        self.worker_registrations: set[tuple[str, str]] = set()
        self.worker_heartbeats = 0
        self.worker_states: list[tuple[str, str, str]] = []
        self.worker_stops: set[tuple[str, str]] = set()

    def register_worker(self, tenant_id: str, worker_id: str) -> None:
        self.worker_registrations.add((tenant_id, worker_id))

    def heartbeat_worker(self, tenant_id: str, worker_id: str) -> None:
        self.worker_heartbeats += 1

    def set_worker_state(self, tenant_id: str, worker_id: str, state: str) -> None:
        self.worker_states.append((tenant_id, worker_id, state))

    def stop_worker(self, tenant_id: str, worker_id: str) -> None:
        self.worker_stops.add((tenant_id, worker_id))

    def claim_next(self, tenant_id: str, worker_id: str, *, lease_seconds: int) -> dict:
        return self.job

    def heartbeat(self, tenant_id: str, job_id: str, worker_id: str, *, lease_seconds: int) -> dict:
        self.heartbeats += 1
        if self.heartbeat_error:
            raise self.heartbeat_error
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
    def __init__(self, error: Exception | None = None, delay: float = 0) -> None:
        self.error = error
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    def resume_session(self, tenant_id: str, session_id: str) -> None:
        self.calls.append((tenant_id, session_id))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error


class StopAfterWait:
    def __init__(self) -> None:
        self.waited = False

    def is_set(self) -> bool:
        return self.waited

    def wait(self, timeout: float | None = None) -> bool:
        self.waited = True
        return True


class IdleQueue(FakeQueue):
    def claim_next(self, tenant_id: str, worker_id: str, *, lease_seconds: int) -> dict | None:
        return None


class FailingClaimQueue(FakeQueue):
    def claim_next(self, tenant_id: str, worker_id: str, *, lease_seconds: int) -> dict:
        raise OSError("simulated database outage")


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

    def test_retryable_service_failure_schedules_bounded_retry(self) -> None:
        queue = FakeQueue()
        service = FakeService(
            IngestionFailed("MEDIA_PROBE_TIMEOUT", {"failure": {"retryable": True}})
        )
        result = self.worker(queue, service).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "retry_scheduled")
        self.assertTrue(queue.failure["retryable"])
        self.assertEqual(queue.failure["retry_delay_seconds"], 8)

    def test_permanent_session_failure_is_dead_lettered(self) -> None:
        queue = FakeQueue()
        service = FakeService(
            IngestionFailed("INGESTION_INVALID_MEDIA", {"failure": {"retryable": False}})
        )
        result = self.worker(queue, service).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "dead")
        self.assertFalse(queue.failure["retryable"])

    def test_stale_worker_does_not_acknowledge_job(self) -> None:
        queue = FakeQueue(lose_on_complete=True)
        result = self.worker(queue, FakeService()).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "lease_lost")
        self.assertFalse(queue.completed)

    def test_repeated_heartbeat_outage_fences_job_acknowledgement(self) -> None:
        queue = FakeQueue(heartbeat_error=OSError("database unavailable"))
        result = self.worker(queue, FakeService(delay=3.2)).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "lease_lost")
        self.assertFalse(queue.completed)
        self.assertGreaterEqual(queue.heartbeats, 2)

    def test_claimed_job_updates_worker_busy_and_idle_state(self) -> None:
        queue = FakeQueue()
        result = self.worker(queue, FakeService()).run_once(self.tenant_id)
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(
            [state for tenant, worker, state in queue.worker_states],
            ["busy", "idle"],
        )

    def test_supervisor_refreshes_idle_worker_heartbeat_and_stops_gracefully(self) -> None:
        queue = IdleQueue()
        worker = self.worker(queue, FakeService())
        stop = Event()
        supervisor = Thread(
            target=run_forever,
            args=(worker, [self.tenant_id], stop),
            kwargs={
                "poll_seconds": 0.005,
                "heartbeat_interval_seconds": 0.01,
                "logger": logging.getLogger("test.worker.heartbeat"),
            },
            daemon=True,
        )
        supervisor.start()
        deadline = time.monotonic() + 2
        while queue.worker_heartbeats < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        stop.set()
        supervisor.join(timeout=2)
        self.assertFalse(supervisor.is_alive(), "worker supervisor did not stop")
        self.assertIn((self.tenant_id, self.worker_id), queue.worker_registrations)
        self.assertGreaterEqual(queue.worker_heartbeats, 1)
        self.assertIn((self.tenant_id, self.worker_id), queue.worker_stops)

    def test_metrics_are_partitioned_by_tenant_and_outcome(self) -> None:
        worker = self.worker(FakeQueue(), FakeService())
        worker.run_once(self.tenant_id)
        snapshot = worker.metrics.snapshot()
        self.assertEqual(snapshot[self.tenant_id]["outcomes"]["completed"], 1)
        self.assertEqual(snapshot[self.tenant_id]["execution_count"], 1)


    def test_aggregate_metrics_snapshot_contains_no_tenant_labels(self) -> None:
        worker = self.worker(FakeQueue(), FakeService())
        other_tenant = str(uuid4())
        worker.metrics.record(self.tenant_id, "completed", 1.25)
        worker.metrics.record(other_tenant, "dead", 0.75)
        snapshot = worker.metrics.aggregate_snapshot()
        self.assertEqual(snapshot["outcomes"], {"completed": 1, "dead": 1})
        self.assertEqual(snapshot["execution_count"], 2)
        self.assertEqual(snapshot["processing_duration_seconds_total"], 2.0)
        self.assertNotIn(self.tenant_id, str(snapshot))
        self.assertNotIn(other_tenant, str(snapshot))

    def test_supervisor_retries_database_failure_instead_of_exiting(self) -> None:
        worker = self.worker(FailingClaimQueue(), FakeService())
        stop = StopAfterWait()
        run_forever(
            worker, [self.tenant_id], stop, poll_seconds=0.01,
            logger=logging.getLogger("test.worker"),
        )
        self.assertTrue(stop.waited)

    def test_invalid_tenant_and_worker_ids_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            IngestionJobWorker(
                queue=FakeQueue(), service=FakeService(), worker_id="not-a-uuid"
            )
        with self.assertRaises(ValueError):
            self.worker(FakeQueue(), FakeService()).run_once("not-a-tenant")


if __name__ == "__main__":
    unittest.main()
