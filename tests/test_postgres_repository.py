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
from voxpipe.ingestion.postgres import sanitize_audit_error_code, sanitize_audit_metadata


ADMIN_DSN = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN")
APP_DSN = "postgresql://voxpipe_app:voxpipe_app@localhost:5432/voxpipe_test"



class AuditMetadataRedactionTests(unittest.TestCase):
    def test_only_allowlisted_non_sensitive_metadata_is_persistable(self) -> None:
        self.assertEqual(
            sanitize_audit_metadata({
                "from": "receiving",
                "to": "cancelled",
                "staging_deleted": True,
                "object_deleted": True,
                "access_token": "secret",
                "authorization": "Bearer secret",
                "transcript": "private text",
                "audio": "private bytes",
                "storage_path": "/private/path",
                "nested": {"token": "secret"},
            }),
            {
                "from": "receiving",
                "to": "cancelled",
                "staging_deleted": True,
                "object_deleted": True,
            },
        )

    def test_error_code_must_be_a_bounded_machine_code(self) -> None:
        self.assertEqual(sanitize_audit_error_code("INGESTION_CLEANUP_FAILED"), "INGESTION_CLEANUP_FAILED")
        self.assertEqual(sanitize_audit_error_code("token=secret"), "AUDIT_ERROR_REDACTED")
        self.assertIsNone(sanitize_audit_error_code(None))


@unittest.skipUnless(ADMIN_DSN, "PostgreSQL integration DSN is not configured")
class PostgresIngestionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg

        cls.admin_dsn = ADMIN_DSN
        cls.app_dsn = APP_DSN
        cls.migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "postgres"

        with psycopg.connect(cls.admin_dsn, autocommit=True) as connection:
            connection.execute("DROP TABLE IF EXISTS transcript_words CASCADE")
            connection.execute("DROP TABLE IF EXISTS transcript_segments CASCADE")
            connection.execute("DROP TABLE IF EXISTS transcript_speakers CASCADE")
            connection.execute("DROP TABLE IF EXISTS transcript_segment_embeddings CASCADE")
            connection.execute("DROP TABLE IF EXISTS transcripts CASCADE")
            connection.execute("DROP TABLE IF EXISTS ingestion_jobs CASCADE")
            connection.execute("DROP TABLE IF EXISTS ingestion_upload_leases CASCADE")
            connection.execute("DROP TABLE IF EXISTS ingestion_audit_events CASCADE")
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
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_sessions, audio_assets, ingestion_jobs, ingestion_upload_leases, ingestion_audit_events TO voxpipe_app"
            )
            connection.execute(
                "GRANT SELECT, INSERT ON transcripts, transcript_speakers, transcript_segments, transcript_words, structured_extractions TO voxpipe_app"
            )

        cls.repository = PostgresIngestionRepository(cls.app_dsn)
        cls.queue = PostgresIngestionJobQueue(cls.app_dsn)

    def setUp(self) -> None:
        import psycopg

        with psycopg.connect(self.admin_dsn) as connection:
            connection.execute("TRUNCATE TABLE transcript_segment_embeddings, structured_extractions, transcript_words, transcript_segments, transcript_speakers, transcripts, ingestion_audit_events, ingestion_upload_leases, ingestion_jobs, audio_assets, ingestion_sessions")

    def test_migration_runner_preserves_semicolons_in_literals_and_dollar_blocks(self) -> None:
        import psycopg

        with tempfile.TemporaryDirectory() as temporary:
            migration = Path(temporary) / "9999_parser_probe.sql"
            migration.write_text(
                """
                CREATE TABLE migration_parser_probe (value text);
                DO $migration$
                BEGIN
                    INSERT INTO migration_parser_probe(value) VALUES ('left;right');
                END;
                $migration$;
                """,
                encoding="utf-8",
            )
            self.assertEqual(apply_migrations(self.admin_dsn, temporary), (9999,))

        with psycopg.connect(self.admin_dsn) as connection:
            row = connection.execute("SELECT value FROM migration_parser_probe").fetchone()
            self.assertEqual(row[0], "left;right")
            connection.execute("DROP TABLE migration_parser_probe")
            connection.execute("DELETE FROM voxpipe_schema_migrations WHERE version = 9999")

    def test_migration_runner_serializes_concurrent_invocations(self) -> None:
        import psycopg

        with tempfile.TemporaryDirectory() as temporary:
            migration = Path(temporary) / "9998_concurrent_probe.sql"
            migration.write_text(
                """
                CREATE TABLE migration_concurrency_probe (id integer PRIMARY KEY);
                INSERT INTO migration_concurrency_probe(id) VALUES (1);
                """,
                encoding="utf-8",
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _: apply_migrations(self.admin_dsn, temporary),
                    range(2),
                ))
            self.assertCountEqual(results, [(9998,), ()])

        with psycopg.connect(self.admin_dsn) as connection:
            rows = connection.execute("SELECT id FROM migration_concurrency_probe").fetchall()
            self.assertEqual(rows, [(1,)])
            connection.execute("DROP TABLE migration_concurrency_probe")
            connection.execute("DELETE FROM voxpipe_schema_migrations WHERE version = 9998")

    def test_transcript_storage_is_append_only_tenant_scoped_and_searchable(self) -> None:
        import psycopg

        tenant_a, tenant_b = str(uuid4()), str(uuid4())
        asset_a = str(uuid4())
        asset_b = str(uuid4())
        now = self.now()
        for tenant, asset in ((tenant_a, asset_a), (tenant_b, asset_b)):
            self.repository.create_asset({
                "asset_id": asset, "tenant_id": tenant, "updated_at": now,
                "created_at": now, "content_type": "audio/wav", "size_bytes": 1,
                "sha256": "a" * 64,
            })

        transcript_id, segment_id, speaker_id = str(uuid4()), str(uuid4()), str(uuid4())
        second_segment_id = str(uuid4())
        with psycopg.connect(self.app_dsn) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_a,))
            connection.execute(
                """INSERT INTO transcripts
                (tenant_id, transcript_id, asset_id, revision, language, text, provider, model,
                 model_version, audio_sha256, generated_at)
                VALUES (%s,%s,%s,1,'fa-IR','سلام جلسه شروع شد. جلسه جلسه مهم','fixture','test-model','1',%s,%s)""",
                (tenant_a, transcript_id, asset_a, "a" * 64, now),
            )
            connection.execute(
                "INSERT INTO transcript_speakers (tenant_id, transcript_id, revision, speaker_id, label) VALUES (%s,%s,1,%s,'speaker_1')",
                (tenant_a, transcript_id, speaker_id),
            )
            connection.execute(
                """INSERT INTO transcript_segments
                (tenant_id, transcript_id, revision, segment_id, start_ms, end_ms, text, speaker_id)
                VALUES (%s,%s,1,%s,0,1200,'سلام جلسه شروع شد',%s),
                       (%s,%s,1,%s,1300,2200,'جلسه جلسه مهم',%s)""",
                (tenant_a, transcript_id, segment_id, speaker_id,
                 tenant_a, transcript_id, second_segment_id, speaker_id),
            )
            connection.execute(
                """INSERT INTO transcript_words
                (tenant_id, transcript_id, revision, segment_id, word_index, start_ms, end_ms, text)
                VALUES (%s,%s,1,%s,0,0,300,'سلام')""",
                (tenant_a, transcript_id, segment_id),
            )
            connection.execute(
                """INSERT INTO transcripts
                (tenant_id, transcript_id, asset_id, revision, language, text, provider, model,
                 model_version, audio_sha256, generated_at)
                VALUES (%s,%s,%s,2,'fa-IR','جلسه نسخه دوم','fixture','test-model','2',%s,%s)""",
                (tenant_a, transcript_id, asset_a, "b" * 64, now),
            )
            connection.execute(
                """INSERT INTO transcript_speakers
                (tenant_id, transcript_id, revision, speaker_id, label)
                VALUES (%s,%s,2,%s,'speaker_1')""",
                (tenant_a, transcript_id, speaker_id),
            )
            connection.execute(
                """INSERT INTO transcript_segments
                (tenant_id, transcript_id, revision, segment_id, start_ms, end_ms, text, speaker_id)
                VALUES (%s,%s,2,%s,0,900,'جلسه نسخه دوم',%s)""",
                (tenant_a, transcript_id, str(uuid4()), speaker_id),
            )

        with psycopg.connect(self.app_dsn) as connection:
            # Missing tenant context must fail closed for every transcript-derived table.
            self.assertEqual(connection.execute("SELECT count(*) FROM transcripts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM transcript_segments").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM transcript_words").fetchone()[0], 0)

        # RLS must also reject writes without a tenant context.
        with psycopg.connect(self.app_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO transcripts
                        (tenant_id, transcript_id, asset_id, revision, language, text,
                         provider, model, audio_sha256, generated_at)
                        VALUES (%s,%s,%s,1,'fa-IR','unauthorized','fixture','test-model',%s,%s)""",
                        (tenant_a, str(uuid4()), asset_a, "a" * 64, now),
                    )

        # A tenant cannot attach its transcript to another tenant's source asset.
        with psycopg.connect(self.app_dsn) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_b,))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO transcripts
                        (tenant_id, transcript_id, asset_id, revision, language, text,
                         provider, model, audio_sha256, generated_at)
                        VALUES (%s,%s,%s,1,'fa-IR','cross-tenant','fixture','test-model',%s,%s)""",
                        (tenant_b, str(uuid4()), asset_a, "a" * 64, now),
                    )

        with psycopg.connect(self.app_dsn) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_b,))
            self.assertEqual(connection.execute("SELECT count(*) FROM transcripts").fetchone()[0], 0)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("UPDATE transcripts SET text='tampered' WHERE transcript_id=%s", (transcript_id,))

        rows = self.repository.search_transcript_segments(
            tenant_a, "جلسه", transcript_id=transcript_id, language="fa-IR",
            speaker_id=speaker_id, start_ms=0, end_ms=3000, limit=10, revision=1,
        )
        self.assertEqual(len(rows), 2)
        # More occurrences of the term rank higher; ties remain stable by time and ID.
        self.assertEqual(str(rows[0]["segment_id"]), second_segment_id)
        self.assertEqual(str(rows[1]["segment_id"]), segment_id)
        self.assertGreater(rows[0]["rank"], rows[1]["rank"])
        self.assertEqual(str(rows[0]["asset_id"]), asset_a)
        self.assertEqual(rows[0]["revision"], 1)
        all_revisions = self.repository.search_transcript_segments(
            tenant_a, "جلسه", transcript_id=transcript_id, asset_id=asset_a,
        )
        self.assertEqual(len(all_revisions), 3)
        self.assertEqual({row["revision"] for row in all_revisions}, {1, 2})
        revision_two = self.repository.search_transcript_segments(
            tenant_a, "جلسه", asset_id=asset_a, revision=2,
        )
        self.assertEqual(len(revision_two), 1)
        self.assertEqual(revision_two[0]["revision"], 2)
        wrong_source = self.repository.search_transcript_segments(
            tenant_a, "جلسه", asset_id=str(uuid4()),
        )
        self.assertEqual(wrong_source, [])
        page = self.repository.search_transcript_segments(
            tenant_a, "جلسه", transcript_id=transcript_id, revision=1, limit=1, offset=1
        )
        self.assertEqual(len(page), 1)
        self.assertEqual(str(page[0]["segment_id"]), segment_id)
        self.assertEqual(
            self.repository.search_transcript_segments(tenant_b, "جلسه"), []
        )
        with self.assertRaises(ValueError):
            self.repository.search_transcript_segments(tenant_a, " ")
        with self.assertRaises(ValueError):
            self.repository.search_transcript_segments(tenant_a, "جلسه", limit=101)
        with self.assertRaises(ValueError):
            self.repository.search_transcript_segments(tenant_a, "جلسه", asset_id="not-a-uuid")
        with self.assertRaises(ValueError):
            self.repository.search_transcript_segments(tenant_a, "جلسه", revision=0)
        with self.assertRaises(ValueError):
            self.repository.search_transcript_segments(tenant_a, "جلسه", revision=True)

        # Asset deletion cascades transcript revisions, segments, speakers and words.
        self.assertTrue(self.repository.delete_asset(tenant_a, asset_a))
        with psycopg.connect(self.app_dsn) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_a,))
            self.assertEqual(connection.execute("SELECT count(*) FROM transcripts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM transcript_words").fetchone()[0], 0)

    def test_transcript_repository_creates_reads_and_preserves_immutable_revisions(self) -> None:
        tenant_id = str(uuid4())
        asset = self.asset(tenant_id)
        self.repository.create_asset({**asset, "sha256": "a" * 64, "content_type": "audio/wav", "size_bytes": 1})
        transcript_id = str(uuid4())
        speaker_id = str(uuid4())
        segment_id = str(uuid4())
        transcript = {
            "tenant_id": tenant_id,
            "transcript_id": transcript_id,
            "asset_id": asset["asset_id"],
            "revision": 1,
            "language": "fa-IR",
            "text": "سلام جلسه شروع شد",
            "provider": "fixture",
            "model": "test-model",
            "model_version": "1",
            "audio_sha256": "a" * 64,
            "generated_at": self.now(),
            "speakers": [{"speaker_id": speaker_id, "label": "speaker_1", "confidence": 0.98}],
            "segments": [{
                "segment_id": segment_id, "start_ms": 0, "end_ms": 1200,
                "text": "سلام جلسه شروع شد", "confidence": 0.99,
                "speaker_id": speaker_id,
                "words": [
                    {"word_index": 0, "start_ms": 0, "end_ms": 300, "text": "سلام"},
                    {"word_index": 1, "start_ms": 350, "end_ms": 700, "text": "جلسه"},
                ],
            }],
        }
        created = self.repository.create_transcript_revision(transcript)
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["segments"][0]["text"], "سلام جلسه شروع شد")
        self.assertEqual(len(created["segments"][0]["words"]), 2)
        self.assertEqual(str(created["speakers"][0]["speaker_id"]), speaker_id)
        self.assertIsNone(self.repository.get_transcript_revision(str(uuid4()), transcript_id))
        repeated = self.repository.create_transcript_revision(transcript)
        self.assertEqual(repeated["revision"], created["revision"])
        self.assertEqual(repeated["text"], created["text"])
        conflicting = {**transcript, "text": "متن متفاوت"}
        with self.assertRaises(IngestionRepositoryConflict):
            self.repository.create_transcript_revision(conflicting)

        second_revision = {
            **transcript,
            "revision": 2,
            "text": "سلام جلسه شروع شد و تمام شد",
            "segments": [{
                **transcript["segments"][0],
                "segment_id": str(uuid4()),
                "end_ms": 1800,
                "text": "سلام جلسه شروع شد و تمام شد",
            }],
        }
        self.repository.create_transcript_revision(second_revision)
        latest = self.repository.get_transcript_revision(tenant_id, transcript_id)
        original = self.repository.get_transcript_revision(tenant_id, transcript_id, 1)
        self.assertEqual(latest["revision"], 2)
        self.assertEqual(original["revision"], 1)
        self.assertEqual(original["text"], "سلام جلسه شروع شد")

    def test_concurrent_transcript_revision_writes_allow_exactly_one_winner(self) -> None:
        tenant_id = str(uuid4())
        asset = self.asset(tenant_id)
        self.repository.create_asset({**asset, "sha256": "c" * 64, "content_type": "audio/wav", "size_bytes": 1})
        transcript = {
            "tenant_id": tenant_id,
            "transcript_id": str(uuid4()),
            "asset_id": asset["asset_id"],
            "revision": 1,
            "language": "fa-IR",
            "text": "یک متن آزمایشی",
            "provider": "fixture",
            "model": "test-model",
            "audio_sha256": "c" * 64,
            "generated_at": self.now(),
            "segments": [{
                "segment_id": str(uuid4()), "start_ms": 0, "end_ms": 500,
                "text": "یک متن آزمایشی",
            }],
        }

        def attempt(_: int):
            return self.repository.create_transcript_revision(transcript)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (1, 2)))
        self.assertEqual(results[0]["revision"], 1)
        self.assertEqual(results[1]["revision"], 1)
        self.assertEqual(results[0]["text"], results[1]["text"])
        stored = self.repository.get_transcript_revision(tenant_id, transcript["transcript_id"])
        self.assertEqual(stored["revision"], 1)
        self.assertEqual(stored["text"], transcript["text"])

    def test_transcript_repository_rejects_unknown_speakers_before_writing(self) -> None:
        tenant_id = str(uuid4())
        asset = self.asset(tenant_id)
        self.repository.create_asset({**asset, "sha256": "b" * 64, "content_type": "audio/wav", "size_bytes": 1})
        transcript = {
            "tenant_id": tenant_id,
            "transcript_id": str(uuid4()),
            "asset_id": asset["asset_id"],
            "revision": 1,
            "language": "fa-IR",
            "text": "متن",
            "provider": "fixture",
            "model": "test-model",
            "audio_sha256": "b" * 64,
            "generated_at": self.now(),
            "segments": [{
                "segment_id": str(uuid4()), "start_ms": 0, "end_ms": 100,
                "text": "متن", "speaker_id": str(uuid4()),
            }],
        }
        with self.assertRaisesRegex(ValueError, "unknown speaker"):
            self.repository.create_transcript_revision(transcript)
        self.assertIsNone(
            self.repository.get_transcript_revision(tenant_id, transcript["transcript_id"])
        )

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
            "upload": {
                "expected_size_bytes": 1,
                "received_size_bytes": 1,
                "expected_sha256": "a" * 64,
            },
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
        self.assertEqual(self.upgrade_versions, (2, 3, 4, 5, 6, 7, 9))
        self.assertEqual(self.noop_versions, ())
        with psycopg.connect(self.admin_dsn) as connection:
            for table in ("ingestion_sessions", "audio_assets", "ingestion_jobs", "ingestion_upload_leases", "ingestion_audit_events"):
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

    def test_recovery_scan_ignores_incomplete_upload_sessions(self) -> None:
        tenant_id = str(uuid4())
        incomplete = self.session(tenant_id=tenant_id, key="postgres-incomplete-upload-0001")
        incomplete["upload"] = {
            "expected_size_bytes": 4096,
            "received_size_bytes": 0,
            "expected_sha256": "a" * 64,
        }
        self.repository.create_or_get_session(incomplete)
        complete = self.session(tenant_id=tenant_id, key="postgres-complete-upload-0001")
        complete["upload"] = {
            "expected_size_bytes": 4096,
            "received_size_bytes": 4096,
            "expected_sha256": "b" * 64,
        }
        self.repository.create_or_get_session(complete)
        recoverable = self.repository.list_recoverable_sessions(
            self.now(), 10, tenant_id=tenant_id
        )
        recovered_ids = {item["session_id"] for item in recoverable}
        self.assertNotIn(incomplete["session_id"], recovered_ids)
        self.assertIn(complete["session_id"], recovered_ids)


    def test_legacy_recovery_scan_skips_sessions_owned_by_durable_queue(self) -> None:
        tenant_id = str(uuid4())
        session = self.session(tenant_id=tenant_id, key="postgres-queue-owned-session-0001")
        session["upload"] = {
            "expected_size_bytes": 512,
            "received_size_bytes": 512,
            "expected_sha256": "c" * 64,
        }
        self.repository.create_or_get_session(session)
        self.queue.enqueue(tenant_id, session["session_id"])
        recoverable = self.repository.list_recoverable_sessions(
            self.now(), 10, tenant_id=tenant_id
        )
        self.assertNotIn(session["session_id"], {item["session_id"] for item in recoverable})


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


    def test_queue_metrics_are_tenant_scoped_and_track_processing_duration(self) -> None:
        session = self.session()
        self.repository.create_or_get_session(session)
        self.queue.enqueue(session["tenant_id"], session["session_id"])
        pending = self.queue.metrics(session["tenant_id"])
        self.assertEqual(pending["pending_depth"], 1)
        self.assertEqual(pending["active_leases"], 0)

        worker_id = str(uuid4())
        claimed = self.queue.claim_next(session["tenant_id"], worker_id)
        self.assertIsNotNone(claimed["started_at"])
        active = self.queue.metrics(session["tenant_id"])
        self.assertEqual(active["pending_depth"], 0)
        self.assertEqual(active["active_leases"], 1)

        self.queue.complete(session["tenant_id"], claimed["job_id"], worker_id)
        completed = self.queue.metrics(session["tenant_id"])
        self.assertEqual(completed["active_leases"], 0)
        self.assertEqual(completed["dead_letter_count"], 0)
        self.assertGreaterEqual(completed["completed_processing_seconds_avg"], 0)
        self.assertEqual(self.queue.metrics(str(uuid4()))["pending_depth"], 0)

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
        self.assertIsNotNone(claimed[0]["started_at"])

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
        queue_metrics = self.queue.metrics(session["tenant_id"])
        self.assertEqual(queue_metrics["retry_count"], 1)
        self.assertEqual(queue_metrics["dead_letter_count"], 1)
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


    def test_structured_extraction_persistence_is_idempotent_tenant_scoped_and_append_only(self) -> None:
        import psycopg
        from voxpipe.structuring.contracts import EvidenceSpan, ExtractionProvenance, StructuredClaim, StructuredIntelligence
        from voxpipe.structuring.postgres import PostgresStructuredIntelligenceRepository

        tenant_id = str(uuid4())
        asset = self.asset(tenant_id)
        self.repository.create_asset({**asset, "sha256": "b" * 64, "content_type": "audio/wav", "size_bytes": 1})
        transcript_id, segment_id = str(uuid4()), str(uuid4())
        self.repository.create_transcript_revision({
            "tenant_id": tenant_id, "transcript_id": transcript_id, "asset_id": asset["asset_id"],
            "revision": 1, "language": "fa-IR", "text": "سلام", "provider": "fixture",
            "model": "test-model", "model_version": "1", "audio_sha256": "b" * 64,
            "generated_at": self.now(), "speakers": [],
            "segments": [{"segment_id": segment_id, "start_ms": 0, "end_ms": 500,
                          "text": "سلام", "confidence": 0.99, "speaker_id": None, "words": []}],
        })
        result = StructuredIntelligence(
            document_id=str(uuid4()), asset_id=asset["asset_id"], transcript_id=transcript_id,
            transcript_revision=1,
            provenance=ExtractionProvenance("fixture", "test-model", "1", "prompt-v1", "1.0.0",
                                             datetime.now(timezone.utc)),
            entities=(StructuredClaim(str(uuid4()), {"name": "سلام", "entity_type": "phrase"}, (EvidenceSpan(segment_id, 0, 4, "سلام"),), 0.9),),
        )
        store = PostgresStructuredIntelligenceRepository(self.app_dsn)
        first = store.create_or_get(tenant_id, "extract-0001", result)
        repeated = store.create_or_get(tenant_id, "extract-0001", result)
        self.assertEqual(first, repeated)
        self.assertEqual(store.get(tenant_id, first["extraction_id"]), first)
        self.assertIsNone(store.get(str(uuid4()), first["extraction_id"]))
        with psycopg.connect(self.app_dsn) as connection:
            connection.execute("SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant_id,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("UPDATE structured_extractions SET result='{}'::jsonb WHERE extraction_id=%s",
                                   (first["extraction_id"],))
        from dataclasses import replace
        from voxpipe.structuring.contracts import StructuredIntelligenceValidationError
        original_claim = result.entities[0]
        fabricated = replace(result, entities=(replace(original_claim, evidence=(EvidenceSpan(segment_id, 0, 4, "جعل"),)),))
        with self.assertRaises(StructuredIntelligenceValidationError):
            store.create_or_get(tenant_id, "fabricated-evidence", fabricated)

        changed = replace(result, entities=(replace(original_claim, value={"name": "متفاوت", "entity_type": "phrase"}),))
        from voxpipe.structuring.postgres import StructuredExtractionConflict, StructuredExtractionSourceNotFound
        with self.assertRaises(StructuredExtractionConflict):
            store.create_or_get(tenant_id, "extract-0001", changed)
        with self.assertRaises(StructuredExtractionSourceNotFound):
            store.create_or_get(str(uuid4()), "cross-tenant", result)


if __name__ == "__main__":
    unittest.main()
