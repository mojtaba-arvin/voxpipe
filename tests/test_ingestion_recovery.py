"""Lease claiming, stale-session recovery, and orphan cleanup tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from voxpipe.ingestion import (
    IngestionFailed,
    IngestionInProgress,
    IngestionOrphanReconciler,
    IngestionRecoveryWorker,
    IngestionRequest,
    IngestionService,
    SQLiteIngestionRepository,
)
from voxpipe.media.probe import MediaMetadata
from voxpipe.storage import LocalFilesystemObjectStore, ObjectNotFoundError


class RecoveryInspector:
    def __init__(self) -> None:
        self.calls = 0

    def inspect(self, path: Path) -> MediaMetadata:
        self.calls += 1
        return MediaMetadata(
            mime_type="audio/wav",
            container_format="wav",
            codec="pcm_s16le",
            duration_ms=1000,
            size_bytes=path.stat().st_size,
            sample_rate_hz=16000,
            channels=1,
            audio_track_index=0,
            bit_depth=16,
            channel_layout="mono",
        )


class RecoveryDecoder:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, path: Path) -> None:
        self.calls += 1


class IngestionRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.staging = root / "staging"
        self.staging.mkdir()
        self.database = root / "metadata.sqlite3"
        self.repository = SQLiteIngestionRepository(self.database)
        self.object_store = LocalFilesystemObjectStore(root / "objects")
        self.path = self.staging / "recovery.wav"
        self.path.write_bytes(b"recovery fixture bytes")
        self.inspector = RecoveryInspector()
        self.decoder = RecoveryDecoder()
        self.service = self.make_service()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_service(self) -> IngestionService:
        return IngestionService(
            staging_root=self.staging,
            repository=self.repository,
            object_store=self.object_store,
            media_inspector=self.inspector,
            decode_validator=self.decoder,
        )

    def request(self) -> IngestionRequest:
        return IngestionRequest(
            tenant_id="7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10",
            idempotency_key="recovery-request-000001",
            staged_path=self.path,
            source={"kind": "file_upload"},
        )

    def make_processing_session_with_expired_lease(self) -> dict:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        session["state"] = "processing"
        session["lease"] = {
            "owner_id": str(uuid4()),
            "expires_at": "2000-01-01T00:00:00.000Z",
            "heartbeat_at": "2000-01-01T00:00:00.000Z",
            "attempt": 1,
        }
        session["updated_at"] = "2000-01-01T00:00:00.000Z"
        return self.repository.update_session(
            session["tenant_id"],
            session["session_id"],
            "completed",
            session,
        )

    def test_repository_lease_claim_is_exclusive_and_expires(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        session["state"] = "processing"
        session.pop("lease", None)
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )

        first = self.repository.claim_session(
            session["tenant_id"],
            session["session_id"],
            str(uuid4()),
            "2026-11-19T00:00:00.000Z",
            "2026-11-19T00:05:00.000Z",
        )
        self.assertIsNotNone(first)
        self.assertEqual(first["lease"]["attempt"], 1)

        second = self.repository.claim_session(
            session["tenant_id"],
            session["session_id"],
            str(uuid4()),
            "2026-11-19T00:01:00.000Z",
            "2026-11-19T00:06:00.000Z",
        )
        self.assertIsNone(second)

        third = self.repository.claim_session(
            session["tenant_id"],
            session["session_id"],
            str(uuid4()),
            "2026-11-19T00:06:00.000Z",
            "2026-11-19T00:11:00.000Z",
        )
        self.assertIsNotNone(third)
        self.assertEqual(third["lease"]["attempt"], 2)


    def test_recovery_ignores_created_sessions_with_incomplete_uploads(self) -> None:
        tenant_id = "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
        session_id = str(uuid4())
        self.repository.create_or_get_session({
            "session_id": session_id,
            "tenant_id": tenant_id,
            "idempotency_key": "unfinished-upload-session-0001",
            "state": "created",
            "updated_at": "2026-11-19T00:00:00.000Z",
            "upload": {
                "expected_size_bytes": 1024,
                "received_size_bytes": 0,
                "expected_sha256": "a" * 64,
            },
        })
        recoverable = self.repository.list_recoverable_sessions(
            "2026-11-19T01:00:00.000Z", limit=10, tenant_id=tenant_id
        )
        self.assertNotIn(session_id, [item["session_id"] for item in recoverable])

    def test_expired_session_lease_is_recovered_without_duplicate_asset(self) -> None:
        original = self.service.ingest(self.request())
        stale = self.make_processing_session_with_expired_lease()
        worker = IngestionRecoveryWorker(
            repository=self.repository,
            service=self.make_service(),
        )

        report = worker.run(limit=10)

        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.skipped, 0)
        recovered = self.repository.get_session(
            original.session["tenant_id"], original.session["session_id"]
        )
        self.assertEqual(recovered["state"], "completed")
        self.assertNotIn("lease", recovered)
        self.assertEqual(recovered["lease"] if "lease" in recovered else None, None)
        self.assertEqual(recovered["asset_id"], original.asset["asset_id"])
        self.assertEqual(
            self.repository.get_asset(recovered["tenant_id"], recovered["asset_id"]),
            original.asset,
        )
        self.assertEqual(self.object_store.head(original.asset["storage"]["object_key"]).sha256,
                         original.asset["integrity"]["digest"])

    def test_recovery_from_finalizing_state_repeats_validation_and_storage(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        self.repository.delete_asset(session["tenant_id"], session["asset_id"])
        self.object_store.delete(result.asset["storage"]["object_key"])
        session["state"] = "finalizing"
        session["lease"] = {
            "owner_id": str(uuid4()),
            "expires_at": "2000-01-01T00:00:00.000Z",
            "heartbeat_at": "2000-01-01T00:00:00.000Z",
            "attempt": 1,
        }
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )

        report = IngestionRecoveryWorker(
            repository=self.repository,
            service=self.make_service(),
        ).run(limit=10)

        self.assertEqual(report.recovered, 1)
        recovered = self.repository.get_session(session["tenant_id"], session["session_id"])
        self.assertEqual(recovered["state"], "completed")
        self.assertIsNotNone(self.repository.get_asset(session["tenant_id"], session["asset_id"]))
        self.assertEqual(
            self.object_store.head(result.asset["storage"]["object_key"]).sha256,
            result.asset["integrity"]["digest"],
        )

    def test_recovery_after_object_write_before_asset_commit(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        self.repository.delete_asset(session["tenant_id"], session["asset_id"])
        session["state"] = "processing"
        session["lease"] = {
            "owner_id": str(uuid4()),
            "expires_at": "2000-01-01T00:00:00.000Z",
            "heartbeat_at": "2000-01-01T00:00:00.000Z",
            "attempt": 1,
        }
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )

        report = IngestionRecoveryWorker(
            repository=self.repository,
            service=self.make_service(),
        ).run(limit=10)

        self.assertEqual(report.recovered, 1)
        recovered = self.repository.get_session(session["tenant_id"], session["session_id"])
        self.assertEqual(recovered["state"], "completed")
        self.assertIsNotNone(self.repository.get_asset(session["tenant_id"], session["asset_id"]))
        self.assertEqual(
            self.object_store.head(result.asset["storage"]["object_key"]).sha256,
            result.asset["integrity"]["digest"],
        )

    def test_live_lease_is_skipped_by_recovery_worker(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        session["state"] = "processing"
        session["lease"] = {
            "owner_id": str(uuid4()),
            "expires_at": "2099-01-01T00:00:00.000Z",
            "heartbeat_at": "2026-11-19T00:00:00.000Z",
            "attempt": 1,
        }
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )
        worker = IngestionRecoveryWorker(repository=self.repository, service=self.make_service())
        report = worker.run(limit=10)
        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped, 0)  # live leases are not listed as recoverable

    def test_missing_staging_file_fails_recovered_session_and_cleans_artifacts(self) -> None:
        result = self.service.ingest(self.request())
        stale = self.make_processing_session_with_expired_lease()
        self.path.unlink()
        with self.assertRaises(IngestionFailed) as context:
            self.make_service().resume_session(stale["tenant_id"], stale["session_id"])
        self.assertEqual(context.exception.code, "INGESTION_STAGED_FILE_MISSING")
        failed = self.repository.get_session(stale["tenant_id"], stale["session_id"])
        self.assertEqual(failed["state"], "failed")
        self.assertNotIn("lease", failed)
        self.assertIsNone(self.repository.get_asset(stale["tenant_id"], stale["asset_id"]))
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(result.asset["storage"]["object_key"])

    def test_expired_session_is_terminal_and_artifacts_are_cleaned(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        session["state"] = "processing"
        session["expires_at"] = "2000-01-01T00:00:00.000Z"
        session["lease"] = {
            "owner_id": str(uuid4()),
            "expires_at": "2000-01-01T00:00:00.000Z",
            "heartbeat_at": "2000-01-01T00:00:00.000Z",
            "attempt": 1,
        }
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )
        with self.assertRaises(IngestionFailed) as context:
            self.make_service().resume_session(session["tenant_id"], session["session_id"])
        self.assertEqual(context.exception.code, "INGESTION_SESSION_EXPIRED")
        expired = self.repository.get_session(session["tenant_id"], session["session_id"])
        self.assertEqual(expired["state"], "expired")
        self.assertNotIn("lease", expired)
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(result.asset["storage"]["object_key"])

    def test_reconciler_removes_s3_staging_without_shared_root(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        staging_key = f"staging/v1/{session['tenant_id']}/{session['session_id']}/source"
        self.object_store.put(staging_key, io.BytesIO(self.path.read_bytes()))
        session["staging_reference"] = staging_key
        session["staging_backend"] = "s3_compatible"
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )

        reconciler = IngestionOrphanReconciler(
            repository=self.repository,
            object_store=self.object_store,
            staging_root=None,
            storage_provider="s3_compatible",
        )
        report = reconciler.run(tenant_id=session["tenant_id"])

        self.assertEqual(report.errors, 0)
        self.assertEqual(report.cleaned, 1)
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(staging_key)
        durable_key = result.asset["storage"]["object_key"]
        self.assertEqual(self.object_store.head(durable_key).sha256, result.asset["integrity"]["digest"])

    def test_reconciler_removes_completed_staging_but_retains_asset(self) -> None:
        result = self.service.ingest(self.request())
        self.assertTrue(self.path.exists())
        reconciler = IngestionOrphanReconciler(
            repository=self.repository,
            object_store=self.object_store,
            staging_root=self.staging,
        )
        report = reconciler.run(limit=10)
        self.assertEqual(report.cleaned, 1)
        self.assertFalse(self.path.exists())
        self.assertIsNotNone(self.repository.get_asset(result.session["tenant_id"], result.asset["asset_id"]))
        self.assertEqual(self.object_store.head(result.asset["storage"]["object_key"]).sha256,
                         result.asset["integrity"]["digest"])
        cleaned = self.repository.get_session(result.session["tenant_id"], result.session["session_id"])
        self.assertIn("staging_cleanup_completed_at", cleaned)

    def test_reconciler_expires_stale_unfinalized_session(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        session["state"] = "created"
        session["expires_at"] = "2000-01-01T00:00:00.000Z"
        self.repository.update_session(session["tenant_id"], session["session_id"], "completed", session)
        report = IngestionOrphanReconciler(
            repository=self.repository, object_store=self.object_store, staging_root=self.staging
        ).run(limit=10)
        expired = self.repository.get_session(session["tenant_id"], session["session_id"])
        self.assertEqual(expired["state"], "expired")
        self.assertEqual(report.cleaned, 1)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.repository.get_asset(session["tenant_id"], session["asset_id"]))

    def test_retention_expiry_deletes_object_metadata_and_audits(self) -> None:
        import json
        import sqlite3

        result = self.service.ingest(self.request())
        asset = dict(result.asset)
        asset["retention_policy_id"] = "test-short-retention"
        asset["retention_expires_at"] = "2000-01-01T00:00:00.000Z"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE audio_assets SET payload_json = ? WHERE tenant_id = ? AND asset_id = ?",
                (json.dumps(asset), asset["tenant_id"], asset["asset_id"]),
            )
        report = IngestionOrphanReconciler(
            repository=self.repository, object_store=self.object_store, staging_root=self.staging
        ).run(limit=10)
        self.assertEqual(report.retention_deleted, 1)
        self.assertEqual(report.errors, 0)
        self.assertIsNone(self.repository.get_asset(asset["tenant_id"], asset["asset_id"]))
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(asset["storage"]["object_key"])
        with sqlite3.connect(self.database) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM ingestion_audit_events WHERE event_type = 'asset.retention_deleted'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_orphan_reconciler_cleans_failed_session_artifacts(self) -> None:
        result = self.service.ingest(self.request())
        session = dict(result.session)
        session["state"] = "failed"
        session["failure"] = {
            "code": "INGESTION_CLEANUP_FAILED",
            "retryable": False,
            "detail": "simulated cleanup failure",
        }
        self.repository.update_session(
            session["tenant_id"], session["session_id"], "completed", session
        )

        reconciler = IngestionOrphanReconciler(
            repository=self.repository,
            object_store=self.object_store,
            staging_root=self.staging,
        )
        report = reconciler.run(limit=10)

        self.assertEqual(report.cleaned, 1)
        self.assertEqual(report.errors, 0)
        self.assertIsNone(self.repository.get_asset(session["tenant_id"], session["asset_id"]))
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(result.asset["storage"]["object_key"])


if __name__ == "__main__":
    unittest.main()
