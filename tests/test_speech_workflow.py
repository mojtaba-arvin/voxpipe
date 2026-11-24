import hashlib
import io
import time
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from voxpipe.speech.contracts import (
    Transcript, TranscriptProvenance, TranscriptSegment, TranscriptWord,
    TranscriptionError, TranscriptionErrorCode,
)
from voxpipe.storage.ports import ObjectInfo
from voxpipe.workflows.postgres import WorkflowLeaseLost
from voxpipe.workflows.runner import WorkflowHandlerError, WorkflowStepContext
from voxpipe.workflows.speech import SpeechTranscriptionWorkflowHandler

AUDIO = b"voxpipe test audio"
DIGEST = hashlib.sha256(AUDIO).hexdigest()
TENANT = str(uuid4())
ASSET = str(uuid4())
TRANSCRIPT = str(uuid4())
WORKFLOW = str(uuid4())
STEP = str(uuid4())


class FakeStorage:
    def __init__(self, *, digest=DIGEST, data=AUDIO):
        self.digest = digest
        self.data = data
        self.opens = 0
    def head(self, key):
        return ObjectInfo(key, len(AUDIO), self.digest)
    def open(self, key):
        self.opens += 1
        return io.BytesIO(self.data)


class FakeRepository:
    def __init__(self, asset="default"):
        self.asset = {
            "tenant_id": TENANT, "asset_id": ASSET,
            "media": {"mime_type": "audio/wav", "duration_ms": 1000, "size_bytes": len(AUDIO)},
            "integrity": {"digest": DIGEST},
            "storage": {"object_key": "tenant/audio/source"},
        } if asset == "default" else asset
        self.saved = []
    def get_transcript_revision(self, tenant_id, transcript_id, revision):
        for item in self.saved:
            if item["tenant_id"] == tenant_id and item["transcript_id"] == transcript_id and item["revision"] == revision:
                return item
        return None
    def get_asset(self, tenant_id, asset_id):
        if tenant_id == TENANT and asset_id == ASSET:
            return self.asset
        return None
    def create_transcript_revision(self, payload):
        self.saved.append(payload)
        return payload


class FakeAdapter:
    provider_name = "fake-local-stt"
    def __init__(self, error=None, delay=0):
        self.error = error
        self.delay = delay
        self.requests = []
    def transcribe(self, request):
        self.requests.append(request)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        now = datetime.now(timezone.utc)
        segment_id = str(uuid4())
        return Transcript(
            transcript_id=str(uuid4()), asset_id=request.asset_id,
            language="en", text="hello world",
            segments=(TranscriptSegment(
                segment_id=segment_id, start_ms=0, end_ms=1000, text="hello world",
                words=(TranscriptWord(0, 400, "hello"), TranscriptWord(450, 900, "world")),
            ),),
            speakers=(),
            provenance=TranscriptProvenance(
                provider=self.provider_name, model="fake-model",
                audio_sha256=DIGEST, generated_at=now,
            ),
            created_at=now,
        )


def context(*, heartbeat=None, refs=None):
    return WorkflowStepContext(
        tenant_id=TENANT, workflow_id=WORKFLOW, step_id=STEP,
        step_name="speech.transcribe", attempt=1,
        input_refs=refs or {
            "asset_id": ASSET, "transcript_id": TRANSCRIPT, "revision": 1,
            "language_hint": "en", "word_timestamps": True,
        },
        heartbeat=heartbeat or (lambda: {"lease": "owned"}),
    )


class SpeechTranscriptionWorkflowTests(unittest.TestCase):
    def test_local_or_hosted_adapter_uses_same_workflow_and_persists_immutable_revision(self):
        repo, storage, adapter = FakeRepository(), FakeStorage(), FakeAdapter()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=repo, object_store=storage, adapter=adapter,
            heartbeat_interval_seconds=1,
        )
        result = handler(context())
        self.assertEqual(result["transcript_id"], TRANSCRIPT)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["provider"], "fake-local-stt")
        self.assertEqual(result["segment_count"], 1)
        self.assertEqual(result["word_count"], 2)
        self.assertEqual(repo.saved[0]["transcript_id"], TRANSCRIPT)
        self.assertEqual(repo.saved[0]["segments"][0]["words"][1]["text"], "world")
        self.assertEqual(adapter.requests[0].idempotency_key, hashlib.sha256(
            f"voxpipe-stt:{TENANT}:{WORKFLOW}:{STEP}:{TRANSCRIPT}:1".encode()
        ).hexdigest())
        self.assertNotIn("text", result)
        self.assertNotIn("transcript", result)

    def test_retryable_provider_error_preserves_retry_after_for_durable_workflow(self):
        error = TranscriptionError(
            TranscriptionErrorCode.RATE_LIMITED, provider="fake-local-stt",
            retryable=True, retry_after_seconds=7,
        )
        handler = SpeechTranscriptionWorkflowHandler(
            repository=FakeRepository(), object_store=FakeStorage(),
            adapter=FakeAdapter(error=error), heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context())
        self.assertEqual(raised.exception.code, "STT_RATE_LIMITED")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_delay_seconds, 7)

    def test_audio_digest_mismatch_fails_before_provider_invocation(self):
        adapter = FakeAdapter()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=FakeRepository(), object_store=FakeStorage(digest="0" * 64),
            adapter=adapter, heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context())
        self.assertEqual(raised.exception.code, "STT_AUDIO_INTEGRITY_MISMATCH")
        self.assertEqual(adapter.requests, [])

    def test_lease_loss_during_long_transcription_prevents_persistence(self):
        calls = [0]
        def heartbeat():
            calls[0] += 1
            if calls[0] >= 2:
                raise WorkflowLeaseLost("expired")
            return {"lease": "owned"}
        repo = FakeRepository()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=repo, object_store=FakeStorage(),
            adapter=FakeAdapter(delay=.15), heartbeat_interval_seconds=.05,
        )
        with self.assertRaises(WorkflowLeaseLost):
            handler(context(heartbeat=heartbeat))
        self.assertEqual(repo.saved, [])

    def test_unknown_workflow_input_reference_is_rejected(self):
        refs = {"asset_id": ASSET, "transcript_id": TRANSCRIPT, "revision": 1, "raw_audio": "not-allowed"}
        handler = SpeechTranscriptionWorkflowHandler(
            repository=FakeRepository(), object_store=FakeStorage(),
            adapter=FakeAdapter(), heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context(refs=refs))
        self.assertEqual(raised.exception.code, "STT_INPUT_INVALID")

    def test_missing_asset_is_not_retried(self):
        handler = SpeechTranscriptionWorkflowHandler(
            repository=FakeRepository(asset=None), object_store=FakeStorage(),
            adapter=FakeAdapter(), heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context())
        self.assertEqual(raised.exception.code, "STT_ASSET_NOT_FOUND")
        self.assertFalse(raised.exception.retryable)


    def test_opened_object_bytes_are_reverified_after_head_check(self):
        adapter = FakeAdapter()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=FakeRepository(),
            object_store=FakeStorage(data=b"tampered bytes"),
            adapter=adapter, heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context())
        self.assertEqual(raised.exception.code, "STT_AUDIO_INTEGRITY_MISMATCH")
        self.assertEqual(adapter.requests, [])

    def test_audio_size_limit_is_enforced_before_provider_invocation(self):
        adapter = FakeAdapter()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=FakeRepository(), object_store=FakeStorage(),
            adapter=adapter, max_audio_bytes=4, heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context())
        self.assertEqual(raised.exception.code, "STT_AUDIO_TOO_LARGE")
        self.assertEqual(adapter.requests, [])


    def test_replay_reuses_existing_revision_without_resubmitting_audio(self):
        repo, storage, first_adapter = FakeRepository(), FakeStorage(), FakeAdapter()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=repo, object_store=storage, adapter=first_adapter,
            heartbeat_interval_seconds=1,
        )
        first = handler(context())
        self.assertEqual(len(first_adapter.requests), 1)
        self.assertEqual(len(repo.saved), 1)

        second_adapter = FakeAdapter()
        replay_handler = SpeechTranscriptionWorkflowHandler(
            repository=repo, object_store=storage, adapter=second_adapter,
            heartbeat_interval_seconds=1,
        )
        replay = replay_handler(context())
        self.assertEqual(replay, first)
        self.assertEqual(second_adapter.requests, [])
        self.assertEqual(storage.opens, 1)
        self.assertEqual(len(repo.saved), 1)

    def test_existing_revision_with_different_audio_digest_fails_closed(self):
        repo = FakeRepository()
        repo.saved.append({
            "tenant_id": TENANT, "transcript_id": TRANSCRIPT,
            "asset_id": ASSET, "revision": 1, "audio_sha256": "0" * 64,
            "provider": "fake-local-stt", "model": "fake-model",
            "language": "en", "segments": [],
        })
        adapter = FakeAdapter()
        handler = SpeechTranscriptionWorkflowHandler(
            repository=repo, object_store=FakeStorage(), adapter=adapter,
            heartbeat_interval_seconds=1,
        )
        with self.assertRaises(WorkflowHandlerError) as raised:
            handler(context())
        self.assertEqual(raised.exception.code, "STT_REVISION_CONFLICT")
        self.assertEqual(adapter.requests, [])


if __name__ == "__main__":
    unittest.main()
