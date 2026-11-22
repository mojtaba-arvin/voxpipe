"""PostgreSQL-backed durable workflow and step journal.

The journal stores source references and bounded metadata, never raw audio or
transcript content. External side effects must remain idempotent because an
expired lease may cause a step to execute again after process recovery.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID, uuid4


class WorkflowError(RuntimeError):
    code = "WORKFLOW_ERROR"


class WorkflowConflict(WorkflowError):
    code = "WORKFLOW_CONFLICT"


class WorkflowLeaseLost(WorkflowError):
    code = "WORKFLOW_LEASE_LOST"


_FORBIDDEN_KEYS = {
    "audio", "audio_bytes", "transcript", "transcript_text", "raw_transcript",
    "raw_audio", "content", "raw_payload", "authorization", "access_token",
    "refresh_token", "api_key", "secret", "password",
}
_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{field} must be a UUID") from None


def _safe_refs(value: dict[str, Any], field: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")

    def inspect(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    raise ValueError(f"{field} keys must be strings")
                if key.casefold() in _FORBIDDEN_KEYS:
                    raise ValueError(f"{field} cannot contain raw or sensitive payload fields")
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            raise ValueError(f"{field} must contain JSON-compatible values")

    inspect(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must contain finite JSON-compatible values") from None
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError(f"{field} exceeds the 64 KiB limit")
    return encoded


@dataclass(frozen=True, slots=True)
class WorkflowStepSpec:
    step_id: str
    name: str
    input_refs: dict[str, Any]
    max_attempts: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _uuid(self.step_id, "step_id"))
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 128:
            raise ValueError("step name must be a non-empty bounded string")
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        _safe_refs(self.input_refs, "input_refs")


class PostgresWorkflowRepository:
    """Durable sequential step orchestration with RLS, retries, leases and approvals."""

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def create_workflow(
        self, tenant_id: str, workflow_id: str, workflow_type: str, workflow_version: str,
        idempotency_key: str, steps: Iterable[WorkflowStepSpec], *, asset_id: str | None = None,
    ) -> dict[str, Any]:
        tenant = _uuid(tenant_id, "tenant_id")
        workflow = _uuid(workflow_id, "workflow_id")
        asset = _uuid(asset_id, "asset_id") if asset_id is not None else None
        for field, value, limit in (
            ("workflow_type", workflow_type, 64), ("workflow_version", workflow_version, 64),
            ("idempotency_key", idempotency_key, 256),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{field} must be a non-empty bounded string")
        specs = tuple(steps)
        if not specs or len(specs) > 100 or any(not isinstance(item, WorkflowStepSpec) for item in specs):
            raise ValueError("workflow must contain 1..100 valid steps")
        ids = [item.step_id for item in specs]
        names = [item.name for item in specs]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError("workflow step IDs and names must be unique")
        from psycopg.types.json import Jsonb

        with self._tenant_connection(tenant) as connection:
            inserted = connection.execute(
                """
                INSERT INTO workflow_runs
                    (tenant_id, workflow_id, asset_id, workflow_type, workflow_version,
                     idempotency_key, state)
                VALUES (%s,%s,%s,%s,%s,%s,'pending')
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING workflow_id
                """,
                (tenant, workflow, asset, workflow_type, workflow_version, idempotency_key),
            ).fetchone()
            if inserted is not None:
                for ordinal, spec in enumerate(specs):
                    connection.execute(
                        """
                        INSERT INTO workflow_steps
                            (tenant_id, workflow_id, step_id, ordinal, step_name,
                             state, input_refs, max_attempts)
                        VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
                        """,
                        (tenant, workflow, spec.step_id, ordinal, spec.name,
                         Jsonb(spec.input_refs), spec.max_attempts),
                    )
            else:
                existing = connection.execute(
                    """
                    SELECT workflow_id, asset_id, workflow_type, workflow_version
                    FROM workflow_runs
                    WHERE tenant_id=%s AND idempotency_key=%s
                    """,
                    (tenant, idempotency_key),
                ).fetchone()
                if existing is None:
                    raise WorkflowConflict("workflow idempotency conflict is not visible in tenant scope")
                prior_steps = connection.execute(
                    """
                    SELECT step_id, ordinal, step_name, input_refs, max_attempts
                    FROM workflow_steps WHERE tenant_id=%s AND workflow_id=%s ORDER BY ordinal
                    """,
                    (tenant, existing["workflow_id"]),
                ).fetchall()
                expected = [
                    {"step_id": spec.step_id, "ordinal": index, "step_name": spec.name,
                     "input_refs": spec.input_refs, "max_attempts": spec.max_attempts}
                    for index, spec in enumerate(specs)
                ]
                actual = [
                    {"step_id": str(row["step_id"]), "ordinal": row["ordinal"], "step_name": row["step_name"],
                     "input_refs": row["input_refs"], "max_attempts": row["max_attempts"]}
                    for row in prior_steps
                ]
                if (str(existing["workflow_id"]) != workflow or (str(existing["asset_id"]) if existing["asset_id"] is not None else None) != asset
                        or existing["workflow_type"] != workflow_type
                        or existing["workflow_version"] != workflow_version or actual != expected):
                    raise WorkflowConflict("idempotency key was reused with different workflow content")
        return self.get_workflow(tenant, workflow)

    @staticmethod
    def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
        serialized = dict(row)
        for key, value in serialized.items():
            if isinstance(value, UUID):
                serialized[key] = str(value)
        return serialized

    def get_workflow(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        tenant = _uuid(tenant_id, "tenant_id")
        workflow = _uuid(workflow_id, "workflow_id")
        with self._tenant_connection(tenant) as connection:
            run = connection.execute(
                "SELECT * FROM workflow_runs WHERE tenant_id=%s AND workflow_id=%s",
                (tenant, workflow),
            ).fetchone()
            if run is None:
                return None
            steps = connection.execute(
                "SELECT * FROM workflow_steps WHERE tenant_id=%s AND workflow_id=%s ORDER BY ordinal",
                (tenant, workflow),
            ).fetchall()
        return {"workflow": self._serialize_row(run), "steps": [self._serialize_row(step) for step in steps]}


    def list_ready_workflows(self, tenant_id: str, *, limit: int = 100) -> tuple[str, ...]:
        """List tenant-scoped workflows with a claimable or expired step."""
        tenant = _uuid(tenant_id, "tenant_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT r.workflow_id
                FROM workflow_runs AS r
                WHERE r.tenant_id=%s AND r.state IN ('pending','running')
                  AND EXISTS (
                    SELECT 1 FROM workflow_steps AS candidate
                    WHERE candidate.tenant_id=r.tenant_id AND candidate.workflow_id=r.workflow_id
                      AND (
                        (candidate.state='pending' AND candidate.available_at<=now())
                        OR (candidate.state='running' AND candidate.lease_expires_at<=now())
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM workflow_steps AS prior
                        WHERE prior.tenant_id=candidate.tenant_id
                          AND prior.workflow_id=candidate.workflow_id
                          AND prior.ordinal<candidate.ordinal
                          AND prior.state<>'completed'
                      )
                  )
                ORDER BY r.updated_at, r.created_at
                LIMIT %s
                """,
                (tenant, limit),
            ).fetchall()
        return tuple(str(row["workflow_id"]) for row in rows)

    def claim_next_ready_step(
        self, tenant_id: str, worker_id: str, *, lease_seconds: int = 60, scan_limit: int = 100,
    ) -> dict[str, Any] | None:
        """Scheduler-friendly claim loop; row-level lease fencing remains authoritative."""
        tenant = _uuid(tenant_id, "tenant_id")
        worker = _uuid(worker_id, "worker_id")
        for workflow_id in self.list_ready_workflows(tenant, limit=scan_limit):
            claimed = self.claim_next_step(tenant, workflow_id, worker, lease_seconds=lease_seconds)
            if claimed is not None:
                return claimed
        return None

    def claim_next_step(
        self, tenant_id: str, workflow_id: str, worker_id: str, *, lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        tenant = _uuid(tenant_id, "tenant_id")
        workflow = _uuid(workflow_id, "workflow_id")
        worker = _uuid(worker_id, "worker_id")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 3 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        with self._tenant_connection(tenant) as connection:
            run = connection.execute(
                "SELECT state FROM workflow_runs WHERE tenant_id=%s AND workflow_id=%s FOR UPDATE",
                (tenant, workflow),
            ).fetchone()
            if run is None or run["state"] in {"waiting_approval", "completed", "failed", "cancelled"}:
                return None
            connection.execute(
                """
                UPDATE workflow_steps SET state='failed', lease_owner=NULL, lease_expires_at=NULL,
                    last_error_code=COALESCE(last_error_code,'WORKFLOW_RETRY_EXHAUSTED'),
                    completed_at=now(), updated_at=now()
                WHERE tenant_id=%s AND workflow_id=%s AND state='running'
                  AND lease_expires_at<=now() AND attempts>=max_attempts
                """,
                (tenant, workflow),
            )
            candidate = connection.execute(
                """
                SELECT * FROM workflow_steps
                WHERE tenant_id=%s AND workflow_id=%s
                  AND (
                    (state='pending' AND available_at<=now())
                    OR (state='running' AND lease_expires_at<=now() AND attempts<max_attempts)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM workflow_steps AS prior
                    WHERE prior.tenant_id=workflow_steps.tenant_id
                      AND prior.workflow_id=workflow_steps.workflow_id
                      AND prior.ordinal<workflow_steps.ordinal
                      AND prior.state<>'completed'
                  )
                ORDER BY ordinal
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (tenant, workflow),
            ).fetchone()
            if candidate is None:
                self._refresh_state(connection, tenant, workflow)
                return None
            claimed = connection.execute(
                """
                UPDATE workflow_steps SET state='running', attempts=attempts+1,
                    lease_owner=%s, lease_expires_at=now()+make_interval(secs=>%s),
                    started_at=COALESCE(started_at,now()), updated_at=now()
                WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s
                RETURNING *
                """,
                (worker, lease_seconds, tenant, workflow, candidate["step_id"]),
            ).fetchone()
            connection.execute(
                "UPDATE workflow_runs SET state='running', updated_at=now() WHERE tenant_id=%s AND workflow_id=%s",
                (tenant, workflow),
            )
        return self._serialize_row(claimed)

    def complete_step(
        self, tenant_id: str, workflow_id: str, step_id: str, worker_id: str,
        result_refs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tenant, workflow = _uuid(tenant_id, "tenant_id"), _uuid(workflow_id, "workflow_id")
        step, worker = _uuid(step_id, "step_id"), _uuid(worker_id, "worker_id")
        safe = result_refs or {}
        _safe_refs(safe, "result_refs")
        from psycopg.types.json import Jsonb
        with self._tenant_connection(tenant) as connection:
            row = connection.execute(
                """
                UPDATE workflow_steps SET state='completed', result_refs=%s,
                    lease_owner=NULL, lease_expires_at=NULL, completed_at=now(), updated_at=now()
                WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s
                  AND state='running' AND lease_owner=%s AND lease_expires_at>now()
                RETURNING step_id
                """,
                (Jsonb(safe), tenant, workflow, step, worker),
            ).fetchone()
            if row is None:
                raise WorkflowLeaseLost("workflow step lease is absent, expired or owned by another worker")
            self._refresh_state(connection, tenant, workflow)
        result = self.get_workflow(tenant, workflow)
        assert result is not None
        return result

    def fail_step(
        self, tenant_id: str, workflow_id: str, step_id: str, worker_id: str, error_code: str,
        *, retryable: bool, retry_delay_seconds: int = 0,
    ) -> dict[str, Any]:
        tenant, workflow = _uuid(tenant_id, "tenant_id"), _uuid(workflow_id, "workflow_id")
        step, worker = _uuid(step_id, "step_id"), _uuid(worker_id, "worker_id")
        if not isinstance(retry_delay_seconds, int) or isinstance(retry_delay_seconds, bool) or not 0 <= retry_delay_seconds <= 3600:
            raise ValueError("retry_delay_seconds must be between 0 and 3600")
        code = error_code if isinstance(error_code, str) and _ERROR_CODE.fullmatch(error_code) else "WORKFLOW_ERROR_REDACTED"
        with self._tenant_connection(tenant) as connection:
            row = connection.execute(
                """
                SELECT attempts, max_attempts FROM workflow_steps
                WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s
                  AND state='running' AND lease_owner=%s AND lease_expires_at>now()
                FOR UPDATE
                """,
                (tenant, workflow, step, worker),
            ).fetchone()
            if row is None:
                raise WorkflowLeaseLost("workflow step lease is absent, expired or owned by another worker")
            retry = bool(retryable and row["attempts"] < row["max_attempts"])
            connection.execute(
                """
                UPDATE workflow_steps SET state=%s,
                    available_at=now()+make_interval(secs=>%s),
                    lease_owner=NULL, lease_expires_at=NULL, last_error_code=%s,
                    completed_at=CASE WHEN %s='failed' THEN now() ELSE NULL END, updated_at=now()
                WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s
                """,
                ("pending" if retry else "failed", retry_delay_seconds, code,
                 "pending" if retry else "failed", tenant, workflow, step),
            )
            self._refresh_state(connection, tenant, workflow)
        result = self.get_workflow(tenant, workflow)
        assert result is not None
        return result

    def request_approval(
        self, tenant_id: str, workflow_id: str, step_id: str, worker_id: str,
    ) -> dict[str, Any]:
        tenant, workflow = _uuid(tenant_id, "tenant_id"), _uuid(workflow_id, "workflow_id")
        step, worker = _uuid(step_id, "step_id"), _uuid(worker_id, "worker_id")
        with self._tenant_connection(tenant) as connection:
            row = connection.execute(
                """
                UPDATE workflow_steps SET state='waiting_approval', lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=now()
                WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s
                  AND state='running' AND lease_owner=%s AND lease_expires_at>now()
                RETURNING step_id
                """,
                (tenant, workflow, step, worker),
            ).fetchone()
            if row is None:
                raise WorkflowLeaseLost("workflow step lease is absent, expired or owned by another worker")
            self._refresh_state(connection, tenant, workflow)
        result = self.get_workflow(tenant, workflow)
        assert result is not None
        return result

    def decide_approval(
        self, tenant_id: str, workflow_id: str, step_id: str, actor_id: str, *, approved: bool,
    ) -> dict[str, Any]:
        tenant, workflow = _uuid(tenant_id, "tenant_id"), _uuid(workflow_id, "workflow_id")
        step, actor = _uuid(step_id, "step_id"), _uuid(actor_id, "actor_id")
        if not isinstance(approved, bool):
            raise ValueError("approved must be boolean")
        with self._tenant_connection(tenant) as connection:
            row = connection.execute(
                """
                UPDATE workflow_steps SET state=%s, approval_decision=%s, approval_actor_id=%s,
                    approval_decided_at=now(), available_at=now(), completed_at=CASE WHEN %s='failed' THEN now() ELSE NULL END,
                    updated_at=now()
                WHERE tenant_id=%s AND workflow_id=%s AND step_id=%s AND state='waiting_approval'
                RETURNING step_id
                """,
                ("pending" if approved else "failed", "approved" if approved else "rejected",
                 actor, "pending" if approved else "failed", tenant, workflow, step),
            ).fetchone()
            if row is None:
                raise WorkflowConflict("workflow step is not awaiting approval")
            self._refresh_state(connection, tenant, workflow)
        result = self.get_workflow(tenant, workflow)
        assert result is not None
        return result

    def cancel_workflow(self, tenant_id: str, workflow_id: str) -> dict[str, Any]:
        tenant, workflow = _uuid(tenant_id, "tenant_id"), _uuid(workflow_id, "workflow_id")
        with self._tenant_connection(tenant) as connection:
            row = connection.execute(
                "SELECT state FROM workflow_runs WHERE tenant_id=%s AND workflow_id=%s FOR UPDATE",
                (tenant, workflow),
            ).fetchone()
            if row is None:
                raise WorkflowError("workflow was not found")
            if row["state"] not in {"completed", "failed", "cancelled"}:
                connection.execute(
                    """
                    UPDATE workflow_steps SET state='cancelled', lease_owner=NULL,
                        lease_expires_at=NULL, updated_at=now()
                    WHERE tenant_id=%s AND workflow_id=%s
                      AND state IN ('pending','running','waiting_approval')
                    """,
                    (tenant, workflow),
                )
                connection.execute(
                    "UPDATE workflow_runs SET state='cancelled', updated_at=now(), completed_at=now() WHERE tenant_id=%s AND workflow_id=%s",
                    (tenant, workflow),
                )
        result = self.get_workflow(tenant, workflow)
        assert result is not None
        return result

    @staticmethod
    def _refresh_state(connection, tenant: str, workflow: str) -> None:
        states = [row["state"] for row in connection.execute(
            "SELECT state FROM workflow_steps WHERE tenant_id=%s AND workflow_id=%s",
            (tenant, workflow),
        ).fetchall()]
        if any(state == "failed" for state in states):
            state = "failed"
        elif any(state == "waiting_approval" for state in states):
            state = "waiting_approval"
        elif states and all(state == "completed" for state in states):
            state = "completed"
        elif any(state == "running" for state in states):
            state = "running"
        else:
            state = "pending"
        connection.execute(
            "UPDATE workflow_runs SET state=%s, updated_at=now(), completed_at=CASE WHEN %s IN ('completed','failed') THEN now() ELSE NULL END WHERE tenant_id=%s AND workflow_id=%s",
            (state, state, tenant, workflow),
        )

    @contextmanager
    def _tenant_connection(self, tenant_id: str):
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_id,))
            yield connection
