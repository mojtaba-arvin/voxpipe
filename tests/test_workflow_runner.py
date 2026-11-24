import unittest
from threading import Event

from voxpipe.workflows.runner import (
    PostgresWorkflowWorker,
    WorkflowApprovalRequired,
    WorkflowHandlerError,
    WorkflowHandlerRegistry,
)
from voxpipe.workflows.postgres import WorkflowLeaseLost


TENANT = "00000000-0000-0000-0000-000000000001"
WORKER = "00000000-0000-0000-0000-000000000002"
WORKFLOW = "00000000-0000-0000-0000-000000000003"
STEP = "00000000-0000-0000-0000-000000000004"


class FakeRepository:
    def __init__(self):
        self.claim = {
            "workflow_id": WORKFLOW, "step_id": STEP, "step_name": "transcribe",
            "attempts": 1, "input_refs": {"asset_id": "asset-ref"},
        }
        self.calls = []

    def claim_next_ready_step(self, tenant_id, worker_id, **kwargs):
        self.calls.append(("claim", tenant_id, worker_id, kwargs))
        claim, self.claim = self.claim, None
        return claim

    def heartbeat_step(self, tenant_id, workflow_id, step_id, worker_id, **kwargs):
        self.calls.append(("heartbeat", tenant_id, workflow_id, step_id, worker_id, kwargs))
        return {"lease_owner": worker_id}

    def complete_step(self, tenant_id, workflow_id, step_id, worker_id, result_refs):
        self.calls.append(("complete", tenant_id, workflow_id, step_id, worker_id, result_refs))
        if result_refs.get("invalid"):
            raise ValueError("sensitive result rejected")
        return {"workflow": {"state": "running"}, "steps": [{"step_id": STEP, "state": "completed"}]}

    def fail_step(self, tenant_id, workflow_id, step_id, worker_id, code, **kwargs):
        self.calls.append(("fail", tenant_id, workflow_id, step_id, worker_id, code, kwargs))
        state = "pending" if kwargs.get("retryable") else "failed"
        return {"workflow": {"state": state}, "steps": [{"step_id": STEP, "state": state}]}

    def request_approval(self, tenant_id, workflow_id, step_id, worker_id):
        self.calls.append(("approval", tenant_id, workflow_id, step_id, worker_id))
        return {"workflow": {"state": "waiting_approval"}}


class WorkflowRunnerTests(unittest.TestCase):
    def worker(self, repository, handler=None):
        registry = WorkflowHandlerRegistry({"transcribe": handler} if handler else {})
        return PostgresWorkflowWorker(repository, registry, worker_id=WORKER)

    def test_executes_registered_handler_and_persists_reference_result(self):
        repository = FakeRepository()
        seen = []
        worker = self.worker(repository, lambda context: seen.append(context) or {"transcript_ref": "ref-1"})
        outcome = worker.run_once(TENANT)
        self.assertEqual(outcome["outcome"], "completed")
        self.assertEqual(seen[0].tenant_id, TENANT)
        self.assertEqual(seen[0].attempt, 1)
        self.assertEqual(repository.calls[-1][0], "complete")

    def test_handler_can_extend_its_active_lease_cooperatively(self):
        repository = FakeRepository()
        def handler(context):
            lease = context.heartbeat()
            self.assertEqual(lease["lease_owner"], WORKER)
            return {"result_ref": "ref-2"}
        outcome = self.worker(repository, handler).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "completed")
        heartbeat = next(call for call in repository.calls if call[0] == "heartbeat")
        self.assertEqual(heartbeat[1:5], (TENANT, WORKFLOW, STEP, WORKER))
        self.assertEqual(heartbeat[5]["lease_seconds"], 60)

    def test_lost_lease_propagates_without_attempting_stale_failure_transition(self):
        repository = FakeRepository()
        def lost_heartbeat(*args, **kwargs):
            raise WorkflowLeaseLost("lease expired")
        repository.heartbeat_step = lost_heartbeat
        def handler(context):
            context.heartbeat()
            return {"result_ref": "never-completed"}
        with self.assertRaises(WorkflowLeaseLost):
            self.worker(repository, handler).run_once(TENANT)
        self.assertFalse(any(call[0] in {"complete", "fail"} for call in repository.calls))

    def test_unknown_handler_fails_without_dynamic_import(self):
        repository = FakeRepository()
        outcome = self.worker(repository).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(repository.calls[-1][5], "WORKFLOW_HANDLER_NOT_REGISTERED")

    def test_retryable_handler_error_uses_explicit_bounded_policy(self):
        repository = FakeRepository()
        def handler(context):
            raise WorkflowHandlerError("TRANSIENT_PROVIDER", retryable=True, retry_delay_seconds=7)
        outcome = self.worker(repository, handler).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "retry_scheduled")
        self.assertEqual(repository.calls[-1][5], "TRANSIENT_PROVIDER")
        self.assertTrue(repository.calls[-1][6]["retryable"])
        self.assertEqual(repository.calls[-1][6]["retry_delay_seconds"], 7)

    def test_permanent_handler_error_fails_without_retry(self):
        repository = FakeRepository()
        def handler(context):
            raise WorkflowHandlerError("INVALID_SOURCE", retryable=False)
        outcome = self.worker(repository, handler).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "failed")
        self.assertFalse(repository.calls[-1][6]["retryable"])

    def test_handler_can_pause_for_durable_human_approval(self):
        repository = FakeRepository()
        def handler(context):
            raise WorkflowApprovalRequired()
        outcome = self.worker(repository, handler).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "waiting_approval")
        self.assertEqual(repository.calls[-1][0], "approval")

    def test_unexpected_handler_exception_does_not_persist_exception_text(self):
        repository = FakeRepository()
        def handler(context):
            raise RuntimeError("api_key=do-not-log")
        outcome = self.worker(repository, handler).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(repository.calls[-1][5], "WORKFLOW_HANDLER_UNEXPECTED")
        self.assertNotIn("api_key", repr(repository.calls))

    def test_invalid_handler_result_fails_closed(self):
        repository = FakeRepository()
        outcome = self.worker(repository, lambda context: ["not", "a", "mapping"]).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(repository.calls[-1][5], "WORKFLOW_HANDLER_INVALID_RESULT")

    def test_repository_rejects_sensitive_result_and_worker_marks_step_failed(self):
        repository = FakeRepository()
        outcome = self.worker(repository, lambda context: {"invalid": True}).run_once(TENANT)
        self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(repository.calls[-1][5], "WORKFLOW_HANDLER_INVALID_RESULT")

    def test_empty_queue_returns_without_claiming_work(self):
        repository = FakeRepository()
        repository.claim = None
        self.assertIsNone(self.worker(repository, lambda context: {}).run_once(TENANT))

    def test_registry_rejects_duplicate_handler_names(self):
        registry = WorkflowHandlerRegistry({"transcribe": lambda context: {}})
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register("transcribe", lambda context: {})

    def test_worker_rejects_non_finite_poll_interval(self):
        worker = self.worker(FakeRepository(), lambda context: {})
        with self.assertRaises(ValueError):
            worker.run_forever(TENANT, Event(), poll_interval_seconds=float("nan"))


    def test_graceful_shutdown_finishes_claimed_step_before_exiting(self):
        repository = FakeRepository()
        stop_event = Event()
        def handler(context):
            stop_event.set()
            return {"transcript_ref": "durable-result"}
        worker = self.worker(repository, handler)

        worker.run_forever(TENANT, stop_event, poll_interval_seconds=0.001)

        claims = [call for call in repository.calls if call[0] == "claim"]
        completions = [call for call in repository.calls if call[0] == "complete"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0][-1], {"transcript_ref": "durable-result"})

    def test_pre_stopped_worker_does_not_claim_new_work(self):
        repository = FakeRepository()
        stop_event = Event()
        stop_event.set()

        self.worker(repository, lambda context: {}).run_forever(TENANT, stop_event)

        self.assertFalse(any(call[0] == "claim" for call in repository.calls))

    def test_idle_worker_observes_shutdown_after_poll_wait(self):
        repository = FakeRepository()
        class StopAfterWait:
            def __init__(self):
                self.stopped = False
                self.waits = 0
            def is_set(self):
                return self.stopped
            def wait(self, timeout):
                self.waits += 1
                self.stopped = True
                return True
        stop_event = StopAfterWait()
        repository.claim = None

        self.worker(repository, lambda context: {}).run_forever(
            TENANT, stop_event, poll_interval_seconds=0.001,
        )

        self.assertEqual(stop_event.waits, 1)
        self.assertEqual(sum(call[0] == "claim" for call in repository.calls), 1)


    def test_worker_recovers_from_transient_repository_outage_with_bounded_backoff(self):
        class FailOnceRepository(FakeRepository):
            def __init__(self):
                super().__init__()
                self.failures = 0
            def claim_next_ready_step(self, tenant_id, worker_id, **kwargs):
                self.failures += 1
                if self.failures == 1:
                    raise RuntimeError("postgresql://secret-password")
                self.claim = None
                return None

        class StopAfterTwoWaits:
            def __init__(self):
                self.waits = []
                self.stopped = False
            def is_set(self):
                return self.stopped
            def wait(self, timeout):
                self.waits.append(timeout)
                if len(self.waits) == 2:
                    self.stopped = True
                return self.stopped

        repository = FailOnceRepository()
        stop_event = StopAfterTwoWaits()
        worker = self.worker(repository)
        with self.assertLogs("voxpipe.workflows.worker", level="WARNING") as captured:
            worker.run_forever(TENANT, stop_event, poll_interval_seconds=0.01, max_backoff_seconds=0.05)
        self.assertEqual(repository.failures, 2)
        self.assertEqual(stop_event.waits, [0.01, 0.01])
        self.assertNotIn("secret-password", " ".join(captured.output))

    def test_worker_rejects_invalid_tenant_before_polling(self):
        class NeverWait:
            def is_set(self): return False
            def wait(self, timeout): self.fail("invalid tenant must fail before waiting")
        with self.assertRaisesRegex(ValueError, "tenant_id must be a UUID"):
            self.worker(FakeRepository()).run_forever("not-a-uuid", NeverWait())

    def test_worker_bounds_max_backoff_configuration(self):
        with self.assertRaisesRegex(ValueError, "max_backoff_seconds"):
            self.worker(FakeRepository()).run_forever(TENANT, Event(), max_backoff_seconds=float("inf"))

if __name__ == "__main__":
    unittest.main()
