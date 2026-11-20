"""Tenant-scoped PostgreSQL durable queue for ingestion work."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID, uuid4


class JobQueueError(Exception):
    code = "INGESTION_JOB_QUEUE_ERROR"


class JobNotFound(JobQueueError):
    code = "INGESTION_JOB_NOT_FOUND"


class JobLeaseLost(JobQueueError):
    code = "INGESTION_JOB_LEASE_LOST"


class PostgresIngestionJobQueue:
    """PostgreSQL queue with SKIP LOCKED claims and expiring worker leases.

    All operations are tenant-scoped and run under forced RLS. A scheduler
    invokes workers per authorized tenant; this queue intentionally provides
    no cross-tenant claim operation.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def enqueue(
        self,
        tenant_id: str,
        session_id: str,
        *,
        max_attempts: int = 5,
        available_in_seconds: int = 0,
    ) -> dict[str, Any]:
        self._validate_limits(max_attempts, available_in_seconds)
        job_id = str(uuid4())
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                INSERT INTO ingestion_jobs
                    (job_id, tenant_id, session_id, state, max_attempts,
                     available_at, created_at, updated_at)
                VALUES (
                    %s, %s, %s, 'pending', %s,
                    now() + make_interval(secs => %s), now(), now()
                )
                ON CONFLICT (tenant_id, session_id) DO NOTHING
                RETURNING *
                """,
                (job_id, tenant_id, session_id, max_attempts, available_in_seconds),
            ).fetchone()
            if row is not None:
                return dict(row)
            existing = connection.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE tenant_id = %s AND session_id = %s
                """,
                (tenant_id, session_id),
            ).fetchone()
            if existing is None:
                raise JobQueueError("job enqueue conflicted without a visible row")
            return dict(existing)

    def get(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE tenant_id = %s AND job_id = %s",
                (tenant_id, job_id),
            ).fetchone()
        return dict(row) if row else None

    def claim_next(
        self,
        tenant_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        self._validate_limits(1, lease_seconds)
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be greater than zero")
        with self._tenant_connection(tenant_id) as connection:
            # A final expired attempt must become terminal instead of remaining
            # leased forever and disappearing from future claim scans.
            expired_jobs = connection.execute(
                """
                SELECT job_id, session_id
                FROM ingestion_jobs
                WHERE tenant_id = %s
                  AND state = 'leased'
                  AND lease_expires_at <= now()
                  AND attempts >= max_attempts
                ORDER BY updated_at ASC
                LIMIT 100
                """,
                (tenant_id,),
            ).fetchall()
            for expired_job in expired_jobs:
                # Keep the cross-runtime lock order stable: session, then job.
                connection.execute(
                    """
                    SELECT session_id FROM ingestion_sessions
                    WHERE tenant_id = %s AND session_id = %s
                    FOR UPDATE
                    """,
                    (tenant_id, expired_job["session_id"]),
                ).fetchone()
                terminal = connection.execute(
                    """
                    UPDATE ingestion_jobs
                    SET state = 'dead',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error_code = COALESCE(last_error_code, 'INGESTION_RETRY_EXHAUSTED'),
                        updated_at = now()
                    WHERE tenant_id = %s AND job_id = %s
                      AND state = 'leased'
                      AND lease_expires_at <= now()
                      AND attempts >= max_attempts
                    RETURNING session_id
                    """,
                    (tenant_id, expired_job["job_id"]),
                ).fetchone()
                if terminal is not None:
                    connection.execute(
                        """
                        UPDATE ingestion_sessions
                        SET state = 'failed',
                            payload = (payload - 'lease') ||
                                jsonb_build_object(
                                    'state', 'failed',
                                    'failure', jsonb_build_object(
                                        'code', 'INGESTION_RETRY_EXHAUSTED',
                                        'retryable', false,
                                        'detail', 'The durable job exhausted its lease attempts.'
                                    ),
                                    'updated_at', to_char(
                                        now() AT TIME ZONE 'UTC',
                                        'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
                                    )
                                ),
                            updated_at = now()
                        WHERE tenant_id = %s AND session_id = %s
                          AND state NOT IN ('completed', 'failed', 'cancelled', 'expired')
                        """,
                        (tenant_id, expired_job["session_id"]),
                    )
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT job_id
                    FROM ingestion_jobs
                    WHERE tenant_id = %s
                      AND attempts < max_attempts
                      AND (
                        (state = 'pending' AND available_at <= now())
                        OR (state = 'leased' AND lease_expires_at <= now())
                      )
                    ORDER BY available_at ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ingestion_jobs AS job
                SET state = 'leased',
                    started_at = now(),
                    attempts = job.attempts + 1,
                    lease_owner = %s,
                    lease_expires_at = now() + make_interval(secs => %s),
                    last_error_code = NULL,
                    updated_at = now()
                FROM candidate
                WHERE job.job_id = candidate.job_id
                  AND job.tenant_id = %s
                RETURNING job.*
                """,
                (tenant_id, worker_id, lease_seconds, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def heartbeat(
        self,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        self._validate_limits(1, lease_seconds)
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be greater than zero")
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE ingestion_jobs
                SET lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE tenant_id = %s AND job_id = %s
                  AND state = 'leased' AND lease_owner = %s
                  AND lease_expires_at > now()
                RETURNING *
                """,
                (lease_seconds, tenant_id, job_id, worker_id),
            ).fetchone()
        if row is None:
            raise JobLeaseLost("job lease is absent, expired, or owned by another worker")
        return dict(row)

    def complete(self, tenant_id: str, job_id: str, worker_id: str) -> dict[str, Any]:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = 'completed', lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = now()
                WHERE tenant_id = %s AND job_id = %s
                  AND state = 'leased' AND lease_owner = %s
                  AND lease_expires_at > now()
                RETURNING *
                """,
                (tenant_id, job_id, worker_id),
            ).fetchone()
        if row is None:
            raise JobLeaseLost("only the current unexpired lease owner can complete a job")
        return dict(row)

    def fail(
        self,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        error_code: str,
        *,
        retryable: bool,
        retry_delay_seconds: int = 5,
    ) -> dict[str, Any]:
        if not error_code or len(error_code) > 64:
            raise ValueError("error_code must contain 1 to 64 characters")
        self._validate_limits(1, retry_delay_seconds)
        with self._tenant_connection(tenant_id) as connection:
            job_ref = connection.execute(
                """
                SELECT session_id FROM ingestion_jobs
                WHERE tenant_id = %s AND job_id = %s
                """,
                (tenant_id, job_id),
            ).fetchone()
            if job_ref is None:
                raise JobLeaseLost("job no longer exists for this tenant")

            # Lock the session before the job, matching API and cancellation paths.
            connection.execute(
                """
                SELECT session_id FROM ingestion_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (tenant_id, job_ref["session_id"]),
            ).fetchone()
            current = connection.execute(
                """
                SELECT session_id, attempts, max_attempts
                FROM ingestion_jobs
                WHERE tenant_id = %s AND job_id = %s
                  AND state = 'leased' AND lease_owner = %s
                  AND lease_expires_at > now()
                FOR UPDATE
                """,
                (tenant_id, job_id, worker_id),
            ).fetchone()
            if current is None:
                raise JobLeaseLost("only the current unexpired lease owner can fail a job")

            will_retry = retryable and current["attempts"] < current["max_attempts"]
            next_state = "pending" if will_retry else "dead"
            row = connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = %s,
                    available_at = CASE
                        WHEN %s THEN now() + make_interval(secs => %s)
                        ELSE available_at
                    END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_code = %s,
                    updated_at = now()
                WHERE tenant_id = %s AND job_id = %s
                RETURNING *
                """,
                (
                    next_state, will_retry, retry_delay_seconds, error_code,
                    tenant_id, job_id,
                ),
            ).fetchone()
            if next_state == "dead":
                connection.execute(
                    """
                    UPDATE ingestion_sessions
                    SET state = 'failed',
                        payload = (payload - 'lease') ||
                            jsonb_build_object(
                                'state', 'failed',
                                'failure', jsonb_build_object(
                                    'code', %s::text,
                                    'retryable', false,
                                    'detail', 'The durable job reached a terminal failure state.'
                                ),
                                'updated_at', to_char(
                                    now() AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
                                )
                            ),
                        updated_at = now()
                    WHERE tenant_id = %s AND session_id = %s
                      AND state NOT IN ('completed', 'failed', 'cancelled', 'expired')
                    """,
                    (error_code, tenant_id, current["session_id"]),
                )
        return dict(row)

    def cancel(self, tenant_id: str, job_id: str) -> bool:
        """Atomically cancel a non-terminal session and its pending/leased job.

        Lock order is session then job, matching the Go API store to avoid
        deadlocks when cancellation requests race across runtimes.
        """
        with self._tenant_connection(tenant_id) as connection:
            job_ref = connection.execute(
                """
                SELECT session_id FROM ingestion_jobs
                WHERE tenant_id = %s AND job_id = %s
                """,
                (tenant_id, job_id),
            ).fetchone()
            if job_ref is None:
                return False

            session = connection.execute(
                """
                SELECT state FROM ingestion_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (tenant_id, job_ref["session_id"]),
            ).fetchone()
            if session is None or session["state"] in {
                "completed", "failed", "cancelled", "expired"
            }:
                return False

            job = connection.execute(
                """
                SELECT state FROM ingestion_jobs
                WHERE tenant_id = %s AND job_id = %s
                  AND state IN ('pending', 'leased')
                FOR UPDATE
                """,
                (tenant_id, job_id),
            ).fetchone()
            if job is None:
                return False

            connection.execute(
                """
                UPDATE ingestion_sessions
                SET state = 'cancelled',
                    payload = (payload - 'lease') ||
                        jsonb_build_object(
                            'state', 'cancelled',
                            'updated_at', to_char(
                                now() AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
                            )
                        ),
                    updated_at = now()
                WHERE tenant_id = %s AND session_id = %s
                """,
                (tenant_id, job_ref["session_id"]),
            )
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = 'cancelled', lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = now()
                WHERE tenant_id = %s AND job_id = %s
                """,
                (tenant_id, job_id),
            )
        return True

    @contextmanager
    def _tenant_connection(self, tenant_id: str) -> Iterator[Any]:
        from psycopg.rows import dict_row
        import psycopg

        try:
            normalized_tenant = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id must be a UUID") from None
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('voxpipe.tenant_id', %s, true)",
                (normalized_tenant,),
            )
            yield connection

    @staticmethod
    def _validate_limits(max_attempts: int, seconds: int) -> None:
        if not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if not 0 <= seconds <= 86400:
            raise ValueError("time interval must be between 0 and 86400 seconds")

    def metrics(self, tenant_id: str) -> dict[str, int | float]:
        """Return tenant-scoped queue health without job payloads or media data."""
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE state = 'pending') AS pending_depth,
                    COALESCE(
                        extract(epoch FROM (
                            now() - (min(created_at) FILTER (WHERE state = 'pending'))
                        )),
                        0
                    ) AS oldest_pending_age_seconds,
                    count(*) FILTER (
                        WHERE state = 'leased' AND lease_expires_at > now()
                    ) AS active_leases,
                    COALESCE(
                        sum(GREATEST(attempts - 1, 0)),
                        0
                    ) AS retry_count,
                    count(*) FILTER (WHERE state = 'dead') AS dead_letter_count,
                    COALESCE(
                        avg(extract(epoch FROM (updated_at - started_at)))
                            FILTER (WHERE state = 'completed' AND started_at IS NOT NULL),
                        0
                    ) AS completed_processing_seconds_avg
                FROM ingestion_jobs
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
        return {
            "pending_depth": int(row["pending_depth"]),
            "oldest_pending_age_seconds": round(float(row["oldest_pending_age_seconds"]), 3),
            "active_leases": int(row["active_leases"]),
            "retry_count": int(row["retry_count"]),
            "dead_letter_count": int(row["dead_letter_count"]),
            "completed_processing_seconds_avg": round(
                float(row["completed_processing_seconds_avg"]), 3
            ),
        }
