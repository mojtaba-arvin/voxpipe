"""Explicit handler registry and supervised worker loop for durable workflows.

The repository owns leases and state transitions. Handlers receive only bounded
source references and must make external side effects idempotent.
"""
from __future__ import annotations

import logging
import math
import re
from uuid import UUID
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable, Mapping

from voxpipe.workflows.postgres import WorkflowLeaseLost


class WorkflowHandlerError(RuntimeError):
    """A handler failure with an explicit retry policy and safe error code."""

    def __init__(self, code: str, *, retryable: bool, retry_delay_seconds: int = 0) -> None:
        super().__init__(code)
        if not isinstance(retryable, bool):
            raise ValueError("retryable must be boolean")
        if (not isinstance(retry_delay_seconds, int) or isinstance(retry_delay_seconds, bool)
                or not 0 <= retry_delay_seconds <= 3600):
            raise ValueError("retry_delay_seconds must be between 0 and 3600")
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", code) else "WORKFLOW_HANDLER_ERROR"
        self.retryable = retryable
        self.retry_delay_seconds = retry_delay_seconds


class WorkflowApprovalRequired(RuntimeError):
    """A handler has reached a durable human-approval boundary."""


@dataclass(frozen=True, slots=True)
class WorkflowStepContext:
    tenant_id: str
    workflow_id: str
    step_id: str
    step_name: str
    attempt: int
    input_refs: dict[str, Any]
    heartbeat: Callable[[], dict[str, Any]]
    lease_seconds: int = 60


WorkflowHandler = Callable[[WorkflowStepContext], dict[str, Any]]


class WorkflowHandlerRegistry:
    """Explicit allowlist of step names to callables; no dynamic imports or eval."""

    def __init__(self, handlers: Mapping[str, WorkflowHandler] | None = None) -> None:
        self._handlers: dict[str, WorkflowHandler] = {}
        for name, handler in (handlers or {}).items():
            self.register(name, handler)

    def register(self, name: str, handler: WorkflowHandler) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("handler name must be a non-empty bounded string")
        if not callable(handler):
            raise TypeError("workflow handler must be callable")
        if name in self._handlers:
            raise ValueError(f"workflow handler {name!r} is already registered")
        self._handlers[name] = handler

    def get(self, name: str) -> WorkflowHandler | None:
        return self._handlers.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


class PostgresWorkflowWorker:
    """Process one claimed step at a time; run multiple instances for concurrency."""

    def __init__(
        self,
        repository: Any,
        registry: WorkflowHandlerRegistry,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        scan_limit: int = 100,
    ) -> None:
        if not isinstance(registry, WorkflowHandlerRegistry):
            raise TypeError("registry must be a WorkflowHandlerRegistry")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 3 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        if not isinstance(scan_limit, int) or isinstance(scan_limit, bool) or not 1 <= scan_limit <= 1000:
            raise ValueError("scan_limit must be between 1 and 1000")
        self._repository = repository
        self._registry = registry
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.scan_limit = scan_limit

    def run_once(self, tenant_id: str) -> dict[str, Any] | None:
        """Claim and execute at most one ready step for the authenticated tenant."""
        claim = self._repository.claim_next_ready_step(
            tenant_id, self.worker_id, lease_seconds=self.lease_seconds, scan_limit=self.scan_limit,
        )
        if claim is None:
            return None

        workflow_id = str(claim["workflow_id"])
        step_id = str(claim["step_id"])
        def heartbeat() -> dict[str, Any]:
            return self._repository.heartbeat_step(
                tenant_id, workflow_id, step_id, self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        context = WorkflowStepContext(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            step_id=step_id,
            step_name=claim["step_name"],
            attempt=claim["attempts"],
            input_refs=claim["input_refs"],
            heartbeat=heartbeat,
            lease_seconds=self.lease_seconds,
        )
        handler = self._registry.get(context.step_name)
        if handler is None:
            state = self._repository.fail_step(
                tenant_id, context.workflow_id, context.step_id, self.worker_id,
                "WORKFLOW_HANDLER_NOT_REGISTERED", retryable=False,
            )
            return {"outcome": "failed", "workflow": state}

        try:
            result_refs = handler(context)
        except WorkflowApprovalRequired:
            state = self._repository.request_approval(
                tenant_id, context.workflow_id, context.step_id, self.worker_id,
            )
            return {"outcome": "waiting_approval", "workflow": state}
        except WorkflowLeaseLost:
            # A stale worker must not attempt any further state transition.
            raise
        except WorkflowHandlerError as exc:
            state = self._repository.fail_step(
                tenant_id, context.workflow_id, context.step_id, self.worker_id, exc.code,
                retryable=exc.retryable, retry_delay_seconds=exc.retry_delay_seconds,
            )
            outcome = "retry_scheduled" if any(
                step.get("state") == "pending" and step.get("step_id") == context.step_id
                for step in state.get("steps", ())
            ) else "failed"
            return {"outcome": outcome, "workflow": state}
        except Exception:
            state = self._repository.fail_step(
                tenant_id, context.workflow_id, context.step_id, self.worker_id,
                "WORKFLOW_HANDLER_UNEXPECTED", retryable=False,
            )
            return {"outcome": "failed", "workflow": state}

        if not isinstance(result_refs, dict):
            state = self._repository.fail_step(
                tenant_id, context.workflow_id, context.step_id, self.worker_id,
                "WORKFLOW_HANDLER_INVALID_RESULT", retryable=False,
            )
            return {"outcome": "failed", "workflow": state}

        try:
            state = self._repository.complete_step(
                tenant_id, context.workflow_id, context.step_id, self.worker_id, result_refs,
            )
        except (TypeError, ValueError):
            state = self._repository.fail_step(
                tenant_id, context.workflow_id, context.step_id, self.worker_id,
                "WORKFLOW_HANDLER_INVALID_RESULT", retryable=False,
            )
            return {"outcome": "failed", "workflow": state}
        return {"outcome": "completed", "workflow": state}

    def run_forever(
        self,
        tenant_id: str,
        stop_event: Event,
        *,
        poll_interval_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        """Run under a process supervisor and recover from transient repository outages.

        Database/lease failures are logged without exception text (which may
        contain DSNs or credentials) and retried with bounded exponential
        backoff. Configuration errors are rejected before entering the loop.
        """
        if not hasattr(stop_event, "is_set") or not hasattr(stop_event, "wait"):
            raise TypeError("stop_event must provide is_set() and wait()")
        if (not isinstance(poll_interval_seconds, (int, float)) or isinstance(poll_interval_seconds, bool)
                or not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0 or poll_interval_seconds > 60):
            raise ValueError("poll_interval_seconds must be between 0 and 60")
        if (not isinstance(max_backoff_seconds, (int, float)) or isinstance(max_backoff_seconds, bool)
                or not math.isfinite(max_backoff_seconds) or max_backoff_seconds <= 0 or max_backoff_seconds > 3600):
            raise ValueError("max_backoff_seconds must be between 0 and 3600")
        try:
            tenant_id = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id must be a UUID from trusted runtime configuration") from None

        logger = logging.getLogger("voxpipe.workflows.worker")
        failure_streak = 0
        while not stop_event.is_set():
            try:
                outcome = self.run_once(tenant_id)
                failure_streak = 0
            except Exception:
                failure_streak += 1
                delay = min(
                    float(max_backoff_seconds),
                    float(poll_interval_seconds) * (2 ** min(failure_streak - 1, 10)),
                )
                logger.warning(
                    "workflow worker repository operation failed; retrying after bounded backoff "
                    "failure_streak=%d delay_seconds=%.3f",
                    failure_streak, delay,
                )
                stop_event.wait(delay)
                continue
            if outcome is None:
                stop_event.wait(poll_interval_seconds)
