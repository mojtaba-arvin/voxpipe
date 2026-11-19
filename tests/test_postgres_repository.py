"""PostgreSQL repository and migration integration tests.

These tests run against the disposable PostgreSQL service in GitHub Actions.
Local runs skip unless VOXPIPE_TEST_POSTGRES_ADMIN_DSN is configured.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from voxpipe.ingestion import (
    IngestionRepositoryConflict,
    JobLeaseLost,
    PostgresIngestionJobQueue,
    PostgresIngestionRepository,
    apply_migrations,
)


ADMIN_DSN = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN")
APP_DSN = "postgresql://voxpipe_app:voxpipe_app@localhost:5432/voxpipe_test"


@unittest.skipUnless(ADMIN_DSN, "PostgreSQL integration DSN is not configured")
class PostgresIngestionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg

        cls.admin_dsn = ADMIN_DSN
        cls.app_dsn = APP_DSN
        cls.migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "postgres"

        with psycopg.connect(cls.admin_dsn, autocommit=True) as connection:
            connection.execute("DROP TABLE IF EXISTS ingestion_jobs CASCADE")
            connection.execute("DROP TABLE IF EXISTS audio_assets CASCADE")
            connection.execute("DROP TABLE IF EXISTS ingestion_sessions CASCADE")
            connection.execute("DROP TABLE IF EXISTS voxpipe_schema_migrations CASCADE")
            role = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = 'voxpipe_app'"
            ).fetchone()
            if role is None:
                connection.execute(
                    "CREATE ROLE voxpipe_app LOGIN PASSWORD 'voxpipe_app' NOSUPERUSER NOBYPASSRLS"
                )
            else:
                connection.execute(
                    "ALTER ROLE voxpipe_app WITH LOGIN PASSWORD 'voxpipe_app' NOSUPERUSER NOBYPASSRLS"
                )

        # Exercise a real upgrade path: v1 first, then v2 from the repository.
        with tempfile.TemporaryDirectory() as temporary:
            version_one = Path(temporary) / "migrations"
            version_one.mkdir()
            shutil.copy2(
                cls.migrations_dir / "0001_create_ingestion_tables.sql",
                version_one / "0001_create_ingestion_tables.sql",
            )
            cls.initial_versions = apply_migrations(cls.admin_dsn, version_one)
        cls.upgrade_versions = apply_migrations(cls.admin_dsn, cls.migrations_dir)
        cls.noop_versions = apply_migrations(cls.admin_dsn, cls.migrations_dir)

        with psycopg.connect(cls.admin_dsn, autocommit=True) as connection:
            connection.execute("GRANT USAGE ON SCHEMA public TO voxpipe_app")
            connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_sessions, audio_assets, ingestion_jobs TO voxpipe_app"
            )

        cls.repository = PostgresIngestionRepository(cls.app_dsn)
        cls.queue = PostgresIngestionJobQueue(cls.app_dsn)

    def setUp(self) -> None:
        import psycopg

        with psycopg.connect(self.admin_dsn) as connection:
            connection.execute("TRUNCATE TABLE ingestion_jobs, audio_assets, ingestion_sessions")

    def tearDown(self) -> None:
        pass

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def session(self, *, tenant_id: str | None = None, key: str = "postgres-idempotency-0001") -> dict:
        timestamp = self.now()
        return {
            "session_id": str(uuid4()),
            "tenant_id": tenant_id or str(uuid4()),
            "idempotency_key": key,
            "state": "created",
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def asset(self, tenant_id: str) -> dict:
        timestamp = self.now()
        return {
            "asset_id": str(uuid4()),
            "tenant_id": tenant_id,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def test_versioned_migrations_upgrade_from_v1_and_are_idempotent(self) -> None:
        import psycopg

        self.assertEqual(self.initial_versions, (1,))
        self.assertEqual(self.upgrade_versions, (2, 3))
        self.assertEqual(self.noop_versions, ())
        with psycopg.connect(self.admin_dsn) as connection:
            for table in ("ingestion_sessions", "audio_assets", "ingestion_jobs"):
                row = connection.execute(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s",
                    (table,),
                ).fetchone()
                self.assertEqual(row, (True, True))

    def test_insert_idempotency_and_tenant_isolation(self) -> None:
        session = self.session()
        created, was_created = self.repository.create_or_get_session(session)
        duplicate, duplicate_created = self.repository.create_or_get_session(
            {**session, "session_id": str(uuid4())}
        )
        self.assertTrue(was_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(created, duplicate)

        other_tenant = str(uuid4())
        self.assertIsNone(self.repository.get_session(other_tenant, session["session_id"]))
        self.assertIsNone(
            self.repository.get_session_by_idempotency(other_tenant, session["idempotency_key"])
        )

    def test_asset_crud_is_tenant_scoped_and_idempotent(self) -> None:
        tenant_id = str(uuid4())
        asset = self.asset(tenant_id)
        created = self.repository.create_asset(asset)
        repeated = self.repository.create_asset(asset)
        self.assertEqual(created, repeated)
        self.assertEqual(self.repository.get_asset(tenant_id, asset["asset_id"]), asset)
        self.assertIsNone(self.repository.get_asset(str(uuid4()), asset["asset_id"]))
        self.assertTrue(self.repository.delete_asset(tenant_id, asset["asset_id"]))
        self.assertFalse(self.repository.delete_asset(tenant_id, asset["asset_id"]))

    def test_concurrent_identical_asset_writes_create_one_record(self) -> None:
        asset = self.asset(str(uuid4()))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.repository.create_asset, [asset, asset]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.repository.get_asset(asset["tenant_id"], asset["asset_id"]), asset)

    def test_claim_lease_is_exclusive_and_can_be_reclaimed_after_expiry(self) -> None:
        session = self.session()
        session["state"] = "processing"
        self.repository.create_or_get_session(session)
        now = "2026-11-19T00:00:00.000Z"
        first = self.repository.claim_session(
            session["tenant_id"], session["session_id"], str(uuid4()),
            now, "2026-11-19T00:05:00.000Z",
        )
        self.assertEqual(first["lease"]["attempt"], 1)
        second = self.repository.claim_session(
            session["tenant_id"], session["session_id"], str(uuid4()),
            "2026-11-19T00:01:00.000Z", "2026-11-19T00:06:00.000Z",
        )
        self.assertIsNone(second)
        third = self.repository.claim_session(
            session["tenant_id"], session["session_id"], str(uuid4()),
            "2026-11-19T00:06:00.000Z", "2026-11-19T00:11:00.000Z",
        )
        self.assertEqual(third["lease"]["attempt"], 2)

    def test_state_compare_and_swap_rolls_back_on_mismatch(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        updated = {**session, "state": "receiving", "updated_at": self.now()}
        with self.assertRaises(IngestionRepositoryConflict):
            self.repository.update_session(
                session["tenant_id"], session["session_id"], "processing", updated
            )
        self.assertEqual(
            self.repository.get_session(session["tenant_id"], session["session_id"])["state"],
            "created",
        )
        saved = self.repository.update_session(
            session["tenant_id"], session["session_id"], "created", updated
        )
        self.assertEqual(saved["state"], "receiving")

    def test_recovery_scans_require_and_enforce_tenant_scope(self) -> None:
        tenant_a, tenant_b = str(uuid4()), str(uuid4())
        session_a = self.session(tenant_id=tenant_a, key="postgres-tenant-a-0001")
        session_b = self.session(tenant_id=tenant_b, key="postgres-tenant-b-0001")
        self.repository.create_or_get_session(session_a)
        self.repository.create_or_get_session(session_b)

        recovered = self.repository.list_recoverable_sessions(self.now(), 10, tenant_id=tenant_a)
        self.assertEqual([item["session_id"] for item in recovered], [session_a["session_id"]])
        by_state = self.repository.list_sessions_by_state("created", 10, tenant_id=tenant_b)
        self.assertEqual([item["session_id"] for item in by_state], [session_b["session_id"]])
        with self.assertRaises(ValueError):
            self.repository.list_recoverable_sessions(self.now(), 10)

    def test_concurrent_duplicate_submissions_create_one_session(self) -> None:
        tenant_id = str(uuid4())
        first = self.session(tenant_id=tenant_id, key="postgres-concurrent-0001")
        second = {**first, "session_id": str(uuid4())}

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.repository.create_or_get_session, [first, second]))

        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(results[0][0], results[1][0])

    def test_rls_hides_rows_without_transaction_local_tenant_context(self) -> None:
        import psycopg

        session = self.session()
        self.repository.create_or_get_session(session)
        with psycopg.connect(self.app_dsn) as connection:
            rows = connection.execute("SELECT session_id FROM ingestion_sessions").fetchall()
        self.assertEqual(rows, [])


    def test_job_enqueue_is_idempotent_and_tenant_scoped(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        first = self.queue.enqueue(session["tenant_id"], session["session_id"])
        duplicate = self.queue.enqueue(session["tenant_id"], session["session_id"])
        self.assertEqual(first["job_id"], duplicate["job_id"])
        self.assertEqual(first["state"], "pending")
        self.assertIsNone(self.queue.get(str(uuid4()), first["job_id"]))

    def test_concurrent_workers_claim_a_job_at_most_once(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        self.queue.enqueue(session["tenant_id"], session["session_id"])
        workers = [str(uuid4()), str(uuid4())]
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(
                lambda worker: self.queue.claim_next(session["tenant_id"], worker),
                workers,
            ))
        claimed = [claim for claim in claims if claim is not None]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["attempts"], 1)

    def test_expired_job_lease_is_reclaimed_and_stale_owner_cannot_ack(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        job = self.queue.enqueue(session["tenant_id"], session["session_id"], max_attempts=3)
        first_worker, second_worker = str(uuid4()), str(uuid4())
        first = self.queue.claim_next(session["tenant_id"], first_worker, lease_seconds=1)
        self.assertEqual(first["job_id"], job["job_id"])
        time.sleep(1.1)
        second = self.queue.claim_next(session["tenant_id"], second_worker, lease_seconds=30)
        self.assertEqual(second["job_id"], job["job_id"])
        self.assertEqual(second["attempts"], 2)
        with self.assertRaises(JobLeaseLost):
            self.queue.complete(session["tenant_id"], job["job_id"], first_worker)
        completed = self.queue.complete(session["tenant_id"], job["job_id"], second_worker)
        self.assertEqual(completed["state"], "completed")

    def test_retryable_failures_are_bounded_and_dead_lettered(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        job = self.queue.enqueue(session["tenant_id"], session["session_id"], max_attempts=2)
        worker = str(uuid4())
        self.queue.claim_next(session["tenant_id"], worker)
        rescheduled = self.queue.fail(
            session["tenant_id"], job["job_id"], worker,
            "PROVIDER_TEMPORARY_FAILURE", retryable=True, retry_delay_seconds=0,
        )
        self.assertEqual(rescheduled["state"], "pending")
        second = self.queue.claim_next(session["tenant_id"], worker)
        self.assertEqual(second["attempts"], 2)
        dead = self.queue.fail(
            session["tenant_id"], job["job_id"], worker,
            "PROVIDER_TEMPORARY_FAILURE", retryable=True, retry_delay_seconds=0,
        )
        self.assertEqual(dead["state"], "dead")
        self.assertEqual(dead["last_error_code"], "PROVIDER_TEMPORARY_FAILURE")
        self.assertEqual(self.repository.get_session(session["tenant_id"], session["session_id"])["state"], "failed")
        self.assertIsNone(self.queue.claim_next(session["tenant_id"], worker))

    def test_non_retryable_failure_dead_letters_immediately(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        job = self.queue.enqueue(session["tenant_id"], session["session_id"])
        worker = str(uuid4())
        self.queue.claim_next(session["tenant_id"], worker)
        failed = self.queue.fail(
            session["tenant_id"], job["job_id"], worker,
            "INGESTION_INVALID_MEDIA", retryable=False,
        )
        self.assertEqual(failed["state"], "dead")
        self.assertEqual(self.repository.get_session(session["tenant_id"], session["session_id"])["state"], "failed")

    def test_expired_final_attempt_dead_letters_job_and_session(self) -> None:
        session = self.session()
        session["state"] = "processing"
        self.repository.create_or_get_session(session)
        job = self.queue.enqueue(session["tenant_id"], session["session_id"], max_attempts=1)
        worker = str(uuid4())
        claimed = self.queue.claim_next(session["tenant_id"], worker, lease_seconds=1)
        self.assertEqual(claimed["job_id"], job["job_id"])
        time.sleep(1.1)
        self.assertIsNone(self.queue.claim_next(session["tenant_id"], str(uuid4())))
        stored_job = self.queue.get(session["tenant_id"], job["job_id"])
        stored_session = self.repository.get_session(session["tenant_id"], session["session_id"])
        self.assertEqual(stored_job["state"], "dead")
        self.assertEqual(stored_session["state"], "failed")
        self.assertEqual(stored_session["failure"]["code"], "INGESTION_RETRY_EXHAUSTED")

    def test_cancel_atomically_cancels_job_and_session(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        job = self.queue.enqueue(session["tenant_id"], session["session_id"])
        self.assertTrue(self.queue.cancel(session["tenant_id"], job["job_id"]))
        self.assertFalse(self.queue.cancel(session["tenant_id"], job["job_id"]))
        self.assertEqual(self.queue.get(session["tenant_id"], job["job_id"])["state"], "cancelled")
        self.assertEqual(
            self.repository.get_session(session["tenant_id"], session["session_id"])["state"],
            "cancelled",
        )

    def test_cancel_leased_job_fences_worker_and_cancels_processing_session(self) -> None:
        session = self.session()
        session["state"] = "processing"
        self.repository.create_or_get_session(session)
        job = self.queue.enqueue(session["tenant_id"], session["session_id"])
        worker = str(uuid4())
        claimed = self.queue.claim_next(session["tenant_id"], worker)
        self.assertEqual(claimed["state"], "leased")
        self.assertTrue(self.queue.cancel(session["tenant_id"], job["job_id"]))
        self.assertEqual(self.queue.get(session["tenant_id"], job["job_id"])["state"], "cancelled")
        self.assertEqual(
            self.repository.get_session(session["tenant_id"], session["session_id"])["state"],
            "cancelled",
        )
        with self.assertRaises(JobLeaseLost):
            self.queue.complete(session["tenant_id"], job["job_id"], worker)

    def test_job_rows_are_hidden_without_tenant_context(self) -> None:
        import psycopg

        session = self.session()
        self.repository.create_or_get_session(session)
        self.queue.enqueue(session["tenant_id"], session["session_id"])
        with psycopg.connect(self.app_dsn) as connection:
            rows = connection.execute("SELECT job_id FROM ingestion_jobs").fetchall()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
