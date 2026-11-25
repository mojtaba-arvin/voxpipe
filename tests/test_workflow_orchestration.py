"""PostgreSQL integration tests for durable workflow orchestration."""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time
from uuid import uuid4

from voxpipe.ingestion import apply_migrations
from voxpipe.workflows import (
    PostgresWorkflowRepository, WorkflowConflict, WorkflowLeaseLost, WorkflowStepSpec,
)

ADMIN_DSN = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN")
APP_DSN = "postgresql://voxpipe_app:voxpipe_app@localhost:5432/voxpipe_test"


@unittest.skipUnless(ADMIN_DSN, "disposable PostgreSQL integration DSN is not configured")
class DurableWorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_migrations(ADMIN_DSN)

    def setUp(self):
        self.repository = PostgresWorkflowRepository(APP_DSN)
        self.tenant = str(uuid4())
        self.workflow = str(uuid4())
        self.worker = str(uuid4())
        self.actor = str(uuid4())
        self.step1 = WorkflowStepSpec(str(uuid4()), "transcribe", {"asset_id": str(uuid4())})
        self.step2 = WorkflowStepSpec(str(uuid4()), "extract", {"transcript_id": str(uuid4()), "revision": 1})

    def create(self, steps=None, key="wf-001"):
        return self.repository.create_workflow(
            self.tenant, self.workflow, "audio-intelligence", "1.0",
            key, steps or (self.step1, self.step2),
        )


    def test_scheduler_claims_independent_ready_workflows(self):
        self.create((self.step1,))
        second_workflow = str(uuid4())
        second_step = WorkflowStepSpec(str(uuid4()), "transcribe-second", {"asset_id": str(uuid4())})
        self.repository.create_workflow(self.tenant, second_workflow, "audio-intelligence",
            "1.0", "scheduler-second", (second_step,))
        ready = self.repository.list_ready_workflows(self.tenant)
        self.assertEqual(set(ready), {self.workflow, second_workflow})
        first = self.repository.claim_next_ready_step(self.tenant, self.worker)
        second = self.repository.claim_next_ready_step(self.tenant, str(uuid4()))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["workflow_id"], second["workflow_id"])
        self.assertEqual(self.repository.list_ready_workflows(self.tenant), ())

    def test_concurrent_independent_workers_claim_unique_workflows(self):
        worker_count = 16
        barrier = Barrier(worker_count)
        candidates = []
        for index in range(worker_count):
            workflow_id = str(uuid4())
            step = WorkflowStepSpec(str(uuid4()), f"concurrent-{index}", {"probe": index})
            self.repository.create_workflow(
                self.tenant, workflow_id, "concurrency-probe", "1.0",
                f"concurrent-claim-{workflow_id}", (step,),
            )
            candidates.append((workflow_id, step.step_id))

        def claim(worker_id):
            repository = PostgresWorkflowRepository(APP_DSN)
            barrier.wait(timeout=10)
            return repository.claim_next_ready_step(self.tenant, worker_id)

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            claims = list(pool.map(lambda _: claim(str(uuid4())), range(worker_count)))

        self.assertEqual(len(claims), worker_count)
        self.assertTrue(all(claim is not None for claim in claims))
        self.assertEqual(len({claim["workflow_id"] for claim in claims}), worker_count)
        self.assertEqual(len({claim["step_id"] for claim in claims}), worker_count)
        self.assertEqual(
            {(claim["workflow_id"], claim["step_id"]) for claim in claims},
            set(candidates),
        )

    def test_workflow_steps_are_durable_and_complete_in_order(self):
        created = self.create()
        self.assertEqual(created["workflow"]["state"], "pending")
        first = self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
        self.assertEqual(first["step_id"], self.step1.step_id)
        self.repository.complete_step(self.tenant, self.workflow, self.step1.step_id,
            self.worker, {"transcript_id": str(uuid4()), "revision": 1})
        second = self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
        self.assertEqual(second["step_id"], self.step2.step_id)
        final = self.repository.complete_step(self.tenant, self.workflow, self.step2.step_id,
            self.worker, {"extraction_id": str(uuid4())})
        self.assertEqual(final["workflow"]["state"], "completed")
        self.assertTrue(all(step["state"] == "completed" for step in final["steps"]))

    def test_later_step_cannot_be_claimed_before_predecessor_completes(self):
        self.create()
        first = self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
        self.assertEqual(first["step_id"], self.step1.step_id)
        self.assertIsNone(self.repository.claim_next_step(self.tenant, self.workflow, str(uuid4())))
        self.repository.complete_step(self.tenant, self.workflow, self.step1.step_id,
            self.worker, {"transcript_id": str(uuid4())})
        next_step = self.repository.claim_next_step(self.tenant, self.workflow, str(uuid4()))
        self.assertEqual(next_step["step_id"], self.step2.step_id)

    def test_idempotent_create_returns_existing_but_rejects_changed_content(self):
        first = self.create(key="same-key")
        second = self.repository.create_workflow(self.tenant, self.workflow, "audio-intelligence",
            "1.0", "same-key", (self.step1, self.step2))
        self.assertEqual(first["workflow"]["workflow_id"], second["workflow"]["workflow_id"])
        changed = WorkflowStepSpec(str(uuid4()), "transcribe", {"asset_id": str(uuid4())})
        with self.assertRaises(WorkflowConflict):
            self.repository.create_workflow(self.tenant, self.workflow, "audio-intelligence",
                "1.0", "same-key", (changed, self.step2))

    def test_retry_is_bounded_and_terminal_after_max_attempts(self):
        step = WorkflowStepSpec(str(uuid4()), "transcribe", {"asset_id": str(uuid4())}, max_attempts=2)
        self.create((step,))
        for attempt in (1, 2):
            claimed = self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
            self.assertEqual(claimed["attempts"], attempt)
            state = self.repository.fail_step(self.tenant, self.workflow, step.step_id,
                self.worker, "MODEL_UNAVAILABLE", retryable=True, retry_delay_seconds=0)
        self.assertEqual(state["workflow"]["state"], "failed")
        self.assertEqual(state["steps"][0]["state"], "failed")

    def test_human_approval_pauses_and_resumes_the_same_step(self):
        step = WorkflowStepSpec(str(uuid4()), "post_invoice", {"invoice_id": str(uuid4())})
        self.create((step,))
        self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
        waiting = self.repository.request_approval(self.tenant, self.workflow, step.step_id, self.worker)
        self.assertEqual(waiting["workflow"]["state"], "waiting_approval")
        self.assertIsNone(self.repository.claim_next_step(self.tenant, self.workflow, self.worker))
        approved = self.repository.decide_approval(self.tenant, self.workflow, step.step_id,
            self.actor, approved=True)
        self.assertEqual(approved["steps"][0]["approval_decision"], "approved")
        self.assertEqual(approved["steps"][0]["approval_actor_id"], self.actor)
        self.assertIsNotNone(approved["steps"][0]["approval_decided_at"])
        resumed = self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
        self.assertEqual(resumed["step_id"], step.step_id)
        completed = self.repository.complete_step(self.tenant, self.workflow, step.step_id,
            self.worker, {"result_ref": "invoice-posting/123"})
        self.assertEqual(completed["workflow"]["state"], "completed")

    def test_rejected_approval_fails_workflow_and_cancellation_fences_steps(self):
        step = WorkflowStepSpec(str(uuid4()), "approve_payment", {"payment_id": str(uuid4())})
        self.create((step,))
        self.repository.claim_next_step(self.tenant, self.workflow, self.worker)
        self.repository.request_approval(self.tenant, self.workflow, step.step_id, self.worker)
        rejected = self.repository.decide_approval(self.tenant, self.workflow, step.step_id,
            self.actor, approved=False)
        self.assertEqual(rejected["workflow"]["state"], "failed")
        other_workflow = str(uuid4())
        self.repository.create_workflow(self.tenant, other_workflow, "audio-intelligence",
            "1.0", "cancel-key", (self.step2,))
        cancelled = self.repository.cancel_workflow(self.tenant, other_workflow)
        self.assertEqual(cancelled["workflow"]["state"], "cancelled")
        self.assertIsNone(self.repository.claim_next_step(self.tenant, other_workflow, self.worker))

    def test_tenant_scope_and_sensitive_payloads_fail_closed(self):
        self.create()
        self.assertIsNone(self.repository.get_workflow(str(uuid4()), self.workflow))
        with self.assertRaises(ValueError):
            WorkflowStepSpec(str(uuid4()), "unsafe", {"nested": {"transcript_text": "private"}})
        with self.assertRaises(ValueError):
            self.repository.create_workflow(self.tenant, str(uuid4()), "unsafe", "1",
                "unsafe", (WorkflowStepSpec(str(uuid4()), "bad", {"audio_bytes": "secret"}),))

    def test_heartbeat_extends_only_a_current_unexpired_worker_lease(self):
        self.create((self.step1,))
        claimed = self.repository.claim_next_step(
            self.tenant, self.workflow, self.worker, lease_seconds=3
        )
        renewed = self.repository.heartbeat_step(
            self.tenant, self.workflow, self.step1.step_id, self.worker, lease_seconds=3
        )
        self.assertEqual(renewed["lease_owner"], self.worker)
        self.assertGreater(renewed["lease_expires_at"], claimed["lease_expires_at"])
        with self.repository._tenant_connection(self.tenant) as connection:
            connection.execute(
                "UPDATE workflow_steps SET lease_expires_at=now()-interval '1 second' "
                "WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s",
                (self.tenant, self.workflow, self.step1.step_id),
            )
        with self.assertRaises(WorkflowLeaseLost):
            self.repository.heartbeat_step(
                self.tenant, self.workflow, self.step1.step_id, self.worker, lease_seconds=3
            )
        new_worker = str(uuid4())
        self.repository.claim_next_step(
            self.tenant, self.workflow, new_worker, lease_seconds=3
        )
        with self.assertRaises(WorkflowLeaseLost):
            self.repository.heartbeat_step(
                self.tenant, self.workflow, self.step1.step_id, self.worker, lease_seconds=3
            )

    def test_expired_lease_is_reclaimed_by_fresh_repository_and_old_worker_is_fenced(self):
        self.create((self.step1,))
        self.repository.claim_next_step(self.tenant, self.workflow, self.worker, lease_seconds=3)
        with self.repository._tenant_connection(self.tenant) as connection:
            connection.execute(
                "UPDATE workflow_steps SET lease_expires_at=now()-interval '1 second' WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s",
                (self.tenant, self.workflow, self.step1.step_id),
            )

        # A replacement worker constructs a fresh repository after the prior
        # process disappears; durable lease state, not in-memory state, drives recovery.
        restarted_repository = PostgresWorkflowRepository(APP_DSN)
        new_worker = str(uuid4())
        reclaimed = restarted_repository.claim_next_step(
            self.tenant, self.workflow, new_worker, lease_seconds=3,
        )
        self.assertEqual(reclaimed["attempts"], 2)
        with self.assertRaises(WorkflowLeaseLost):
            self.repository.complete_step(
                self.tenant, self.workflow, self.step1.step_id, self.worker, {"late": True},
            )
        restarted_repository.complete_step(
            self.tenant, self.workflow, self.step1.step_id, new_worker, {"ok": True},
        )


    def test_repeated_worker_contention_drains_all_workflows_without_duplicates(self):
        worker_count = 8
        workflows_per_worker = 8
        total_workflows = worker_count * workflows_per_worker
        start = Barrier(worker_count)
        candidates = set()

        for index in range(total_workflows):
            workflow_id = str(uuid4())
            step = WorkflowStepSpec(str(uuid4()), "process", {"sequence": index})
            self.repository.create_workflow(
                self.tenant,
                workflow_id,
                "sustained-contention-probe",
                "1.0",
                f"sustained-contention-{workflow_id}",
                (step,),
            )
            candidates.add((workflow_id, step.step_id))

        def drain(worker_id):
            repository = PostgresWorkflowRepository(APP_DSN)
            start.wait(timeout=10)
            completed = []
            for _ in range(workflows_per_worker):
                claim = None
                for _attempt in range(50):
                    claim = repository.claim_next_ready_step(
                        self.tenant, worker_id, scan_limit=total_workflows,
                    )
                    if claim is not None:
                        break
                    time.sleep(0.01)
                if claim is None:
                    raise AssertionError("worker could not claim its assigned workflow")
                completed.append((claim["workflow_id"], claim["step_id"]))
                repository.complete_step(
                    self.tenant,
                    claim["workflow_id"],
                    claim["step_id"],
                    worker_id,
                    {"result_ref": f"completed/{claim['workflow_id']}"},
                )
                # Keep workers overlapping across repeated claim/complete rounds
                # instead of measuring only a single synchronized claim burst.
                time.sleep(0.005)
            return completed

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            batches = list(pool.map(lambda _: drain(str(uuid4())), range(worker_count)))

        completed = [item for batch in batches for item in batch]
        self.assertEqual(len(completed), total_workflows)
        self.assertEqual(len(set(completed)), total_workflows)
        self.assertEqual(set(completed), candidates)
        for workflow_id, _step_id in candidates:
            state = self.repository.get_workflow(self.tenant, workflow_id)
            self.assertEqual(state["workflow"]["state"], "completed")
            self.assertEqual(state["steps"][0]["state"], "completed")


if __name__ == "__main__":
    unittest.main()
