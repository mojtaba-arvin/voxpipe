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


if __name__ == "__main__":
    unittest.main()
