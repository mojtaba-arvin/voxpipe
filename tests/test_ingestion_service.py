"""End-to-end batch ingestion orchestration tests with local adapters."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from voxpipe.ingestion import (
    IngestionFailed,
    IngestionIdempotencyConflict,
    IngestionInProgress,
    IngestionRequest,
    IngestionRequestError,
    IngestionService,
    IngestionRepositoryConflict,
    SQLiteIngestionRepository,
)
from voxpipe.media.probe import MediaInspectionError, MediaMetadata
from voxpipe.storage import LocalFilesystemObjectStore, ObjectInfo, ObjectNotFoundError, StorageIOError


class FakeMediaInspector:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def inspect(self, path: Path) -> MediaMetadata:
        self.calls += 1
        if self.error:
            raise self.error
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


class SizeMismatchInspector(FakeMediaInspector):
    def inspect(self, path: Path) -> MediaMetadata:
        metadata = super().inspect(path)
        return MediaMetadata(
            mime_type=metadata.mime_type,
            container_format=metadata.container_format,
            codec=metadata.codec,
            duration_ms=metadata.duration_ms,
            size_bytes=metadata.size_bytes + 1,
            sample_rate_hz=metadata.sample_rate_hz,
            channels=metadata.channels,
            audio_track_index=metadata.audio_track_index,
            bit_depth=metadata.bit_depth,
            channel_layout=metadata.channel_layout,
        )


class FakeDecodeValidator:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def validate(self, path: Path) -> None:
        self.calls += 1
        if self.error:
            raise self.error


class FailingObjectStore:
    def put(self, key, source, *, expected_sha256=None, overwrite=False):
        raise StorageIOError("simulated object storage failure")

    def open(self, key):
        raise ObjectNotFoundError("object does not exist")

    def head(self, key):
        raise ObjectNotFoundError("object does not exist")

    def delete(self, key):
        return False


class CorruptObjectStore:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def put(self, key, source, *, expected_sha256=None, overwrite=False):
        stored = self.wrapped.put(
            key, source, expected_sha256=expected_sha256, overwrite=overwrite
        )
        return ObjectInfo(key=stored.key, size_bytes=stored.size_bytes, sha256="0" * 64)

    def open(self, key):
        return self.wrapped.open(key)

    def head(self, key):
        return self.wrapped.head(key)

    def delete(self, key):
        return self.wrapped.delete(key)


class FailingAssetRepository(SQLiteIngestionRepository):
    def create_asset(self, asset: dict) -> dict:
        raise IngestionRepositoryConflict("simulated metadata write failure")


class BatchIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.staging = root / "staging"
        self.staging.mkdir()
        self.storage_root = root / "objects"
        self.database = root / "metadata.sqlite3"
        self.repository = SQLiteIngestionRepository(self.database)
        self.object_store = LocalFilesystemObjectStore(self.storage_root)
        self.inspector = FakeMediaInspector()
        self.decoder = FakeDecodeValidator()
        self.service = self.make_service()

        self.tenant_id = "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
        self.source = {"kind": "file_upload", "declared_by": "test-client"}
        self.path = self.staging / "sample.wav"
        self.path.write_bytes(b"synthetic staged audio fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_service(
        self,
        *,
        repository: SQLiteIngestionRepository | None = None,
        inspector: FakeMediaInspector | None = None,
        decoder: FakeDecodeValidator | None = None,
        object_store=None,
    ) -> IngestionService:
        return IngestionService(
            staging_root=self.staging,
            repository=repository or self.repository,
            object_store=object_store or self.object_store,
            media_inspector=inspector or self.inspector,
            decode_validator=decoder or self.decoder,
            max_file_size_bytes=1024 * 1024,
        )

    def request(
        self,
        *,
        tenant_id: str | None = None,
        key: str = "ingestion-request-000001",
        path: Path | None = None,
        source: dict | None = None,
        retention_policy_id: str | None = "test-retention-30d",
    ) -> IngestionRequest:
        return IngestionRequest(
            tenant_id=tenant_id or self.tenant_id,
            idempotency_key=key,
            staged_path=path or self.path,
            source=source or self.source,
            retention_policy_id=retention_policy_id,
        )

    def test_ingests_asset_and_persists_valid_contracts(self) -> None:
        result = self.service.ingest(self.request())

        self.assertEqual(result.session["state"], "completed")
        self.assertEqual(result.session["staging_reference"], "sample.wav")
        self.assertNotIn("lease", result.session)
        self.assertEqual(result.asset["state"], "accepted")
        self.assertEqual(result.session["asset_id"], result.asset["asset_id"])
        self.assertEqual(result.asset["tenant_id"], self.tenant_id)
        self.assertEqual(result.asset["integrity"]["algorithm"], "sha256")
        self.assertEqual(result.asset["media"]["sample_rate_hz"], 16000)
        self.assertEqual(self.inspector.calls, 1)
        self.assertEqual(self.decoder.calls, 1)

        asset_schema = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts/schemas/audio-asset.v1.schema.json").read_text()
        )
        session_schema = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts/schemas/ingestion-session.v1.schema.json").read_text()
        )
        Draft202012Validator(asset_schema, format_checker=FormatChecker()).validate(result.asset)
        Draft202012Validator(session_schema, format_checker=FormatChecker()).validate(result.session)

        stored = self.object_store.head(result.asset["storage"]["object_key"])
        self.assertEqual(stored.sha256, result.asset["integrity"]["digest"])
        self.assertEqual(stored.size_bytes, result.asset["media"]["size_bytes"])

    def test_idempotent_replay_returns_existing_asset_without_reprocessing(self) -> None:
        first = self.service.ingest(self.request())
        second = self.service.ingest(self.request())

        self.assertEqual(first.session["session_id"], second.session["session_id"])
        self.assertEqual(first.asset, second.asset)
        self.assertEqual(self.inspector.calls, 1)
        self.assertEqual(self.decoder.calls, 1)

    def test_reusing_idempotency_key_for_different_content_is_rejected(self) -> None:
        self.service.ingest(self.request())
        other = self.staging / "different.wav"
        other.write_bytes(b"different staged audio")
        with self.assertRaises(IngestionIdempotencyConflict):
            self.service.ingest(self.request(path=other))

    def test_retention_policy_is_part_of_idempotency_fingerprint(self) -> None:
        self.service.ingest(self.request())
        with self.assertRaises(IngestionIdempotencyConflict):
            self.service.ingest(self.request(retention_policy_id="different-retention-90d"))

    def test_idempotency_scope_is_per_tenant(self) -> None:
        first = self.service.ingest(self.request())
        other_tenant = str(uuid4())
        second = self.service.ingest(self.request(tenant_id=other_tenant))

        self.assertNotEqual(first.session["session_id"], second.session["session_id"])
        self.assertEqual(first.asset["tenant_id"], self.tenant_id)
        self.assertEqual(second.asset["tenant_id"], other_tenant)
        self.assertIsNone(self.repository.get_asset(other_tenant, first.asset["asset_id"]))
        self.assertIsNone(self.repository.get_asset(self.tenant_id, second.asset["asset_id"]))

    def test_rejects_staged_file_outside_private_root(self) -> None:
        outside = Path(self.temporary.name) / "outside.wav"
        outside.write_bytes(b"outside")
        with self.assertRaises(IngestionRequestError):
            self.service.ingest(self.request(path=outside))

    def test_rejects_symlinked_staged_file(self) -> None:
        link = self.staging / "link.wav"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links are unavailable")
        with self.assertRaises(IngestionRequestError):
            self.service.ingest(self.request(path=link))

    def test_rejects_invalid_source_and_idempotency_key(self) -> None:
        with self.assertRaises(IngestionRequestError):
            self.service.ingest(self.request(key="short"))
        with self.assertRaises(IngestionRequestError):
            self.service.ingest(self.request(source={"kind": "file_upload", "unexpected": "value"}))

    def test_probe_failure_marks_session_failed_without_storing_asset(self) -> None:
        inspector = FakeMediaInspector(MediaInspectionError("MEDIA_PROBE_FAILED", "bad media"))
        service = self.make_service(inspector=inspector)
        with self.assertRaises(IngestionFailed) as context:
            service.ingest(self.request())
        self.assertEqual(context.exception.code, "MEDIA_PROBE_FAILED")
        session = self.repository.get_session_by_idempotency(self.tenant_id, "ingestion-request-000001")
        self.assertEqual(session["state"], "failed")
        self.assertNotIn("lease", session)
        self.assertEqual(session["failure"]["code"], "MEDIA_PROBE_FAILED")
        self.assertIsNone(self.repository.get_asset(self.tenant_id, session["asset_id"]))
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(f"tenant/{self.tenant_id}/assets/{session['asset_id']}/source")

    def test_staged_file_change_is_rejected_before_storage(self) -> None:
        service = self.make_service(inspector=SizeMismatchInspector())
        with self.assertRaises(IngestionFailed) as context:
            service.ingest(self.request())
        self.assertEqual(context.exception.code, "MEDIA_FILE_CHANGED_DURING_INGESTION")
        session = self.repository.get_session_by_idempotency(self.tenant_id, "ingestion-request-000001")
        self.assertEqual(session["state"], "failed")
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(f"tenant/{self.tenant_id}/assets/{session['asset_id']}/source")


    def test_decode_failure_marks_session_failed(self) -> None:
        decoder = FakeDecodeValidator(MediaInspectionError("MEDIA_DECODE_FAILED", "truncated"))
        service = self.make_service(decoder=decoder)
        with self.assertRaises(IngestionFailed) as context:
            service.ingest(self.request())
        self.assertEqual(context.exception.code, "MEDIA_DECODE_FAILED")
        session = self.repository.get_session_by_idempotency(self.tenant_id, "ingestion-request-000001")
        self.assertEqual(session["state"], "failed")

    def test_storage_failure_marks_session_failed(self) -> None:
        service = self.make_service(object_store=FailingObjectStore())
        with self.assertRaises(IngestionFailed) as context:
            service.ingest(self.request())
        self.assertEqual(context.exception.code, "STORAGE_IO_ERROR")
        session = self.repository.get_session_by_idempotency(self.tenant_id, "ingestion-request-000001")
        self.assertEqual(session["state"], "failed")
        self.assertTrue(session["failure"]["retryable"])

    def test_stored_object_fingerprint_mismatch_is_compensated(self) -> None:
        service = self.make_service(object_store=CorruptObjectStore(self.object_store))
        with self.assertRaises(IngestionFailed) as context:
            service.ingest(self.request())
        self.assertEqual(context.exception.code, "STORAGE_INTEGRITY_ERROR")
        session = self.repository.get_session_by_idempotency(self.tenant_id, "ingestion-request-000001")
        self.assertEqual(session["state"], "failed")
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(f"tenant/{self.tenant_id}/assets/{session['asset_id']}/source")


    def test_metadata_failure_compensates_stored_object(self) -> None:
        repository = FailingAssetRepository(self.database)
        service = self.make_service(repository=repository)
        with self.assertRaises(IngestionFailed) as context:
            service.ingest(self.request())
        self.assertEqual(context.exception.code, "INGESTION_STATE_CONFLICT")
        session = repository.get_session_by_idempotency(self.tenant_id, "ingestion-request-000001")
        self.assertEqual(session["state"], "failed")
        with self.assertRaises(ObjectNotFoundError):
            self.object_store.head(f"tenant/{self.tenant_id}/assets/{session['asset_id']}/source")

    def test_completed_session_and_asset_survive_repository_reopen(self) -> None:
        result = self.service.ingest(self.request())
        reopened = SQLiteIngestionRepository(self.database)
        session = reopened.get_session(self.tenant_id, result.session["session_id"])
        asset = reopened.get_asset(self.tenant_id, result.asset["asset_id"])
        self.assertEqual(session["state"], "completed")
        self.assertEqual(asset["asset_id"], result.asset["asset_id"])

    def test_duplicate_active_session_is_not_processed_concurrently(self) -> None:
        initial = self.service.ingest(self.request())
        active = dict(initial.session)
        active["state"] = "processing"
        active["session_id"] = str(uuid4())
        active["asset_id"] = str(uuid4())
        active["idempotency_key"] = "ingestion-request-000002"
        active["lease"] = {
            "owner_id": str(uuid4()),
            "expires_at": "2099-01-01T00:00:00.000Z",
            "heartbeat_at": "2026-11-19T00:00:00.000Z",
            "attempt": 1,
        }
        self.repository.create_or_get_session(active)
        request = self.request(key="ingestion-request-000002")
        with self.assertRaises(IngestionInProgress):
            self.service.ingest(request)


if __name__ == "__main__":
    unittest.main()
