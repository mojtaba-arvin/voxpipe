from __future__ import annotations

import os
import unittest
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from scripts.run_retrieval_service import RetrievalHandler, _validate_index, _validate_search, build_runtime
from voxpipe.retrieval.hybrid import SearchCandidate, SearchHit
from voxpipe.retrieval.runtime import LocalHybridRetrievalRuntime, RetrievalIndexNotReady

TENANT = "11111111-1111-4111-8111-111111111111"
TRANSCRIPT = "22222222-2222-4222-8222-222222222222"
ASSET = "33333333-3333-4333-8333-333333333333"
SEGMENT = "44444444-4444-4444-8444-444444444444"


class FakeRepository:
    def __init__(self):
        self.calls = []

    def get_transcript_revision(self, tenant_id, transcript_id, revision):
        self.calls.append((tenant_id, transcript_id, revision))
        return {
            "revision": 2, "asset_id": ASSET, "language": "fa",
            "segments": [{
                "segment_id": SEGMENT, "start_ms": 100, "end_ms": 900,
                "text": "جلسه فروش فردا", "speaker_id": None,
            }],
        }


class FakeEmbeddings:
    def __init__(self):
        self.documents = []

    def embed_documents(self, texts):
        self.documents.extend(texts)
        return tuple(("embedding", text) for text in texts)


class FakeSemantic:
    def __init__(self, available=True):
        self.stored = []
        self.available = available

    def has_indexed_segments(self, tenant_id):
        return self.available

    def store_embedding(self, tenant_id, candidate, embedding):
        self.stored.append((tenant_id, candidate, embedding))


class FakeHybrid:
    def __init__(self, hit):
        self.hit = hit
        self.call = None

    def search(self, query, *, filters, limit):
        self.call = (query, filters, limit)
        return (self.hit,)


class RetrievalServiceTests(unittest.TestCase):
    def test_internal_search_contract_requires_authenticated_tenant_uuid_and_bounds(self):
        request = _validate_search({
            "tenant_id": TENANT, "query": " جلسه فروش ", "limit": 10,
            "asset_id": ASSET, "language": "fa",
        })
        self.assertEqual(request["tenant_id"], TENANT)
        self.assertEqual(request["query"], "جلسه فروش")
        self.assertEqual(request["limit"], 10)
        with self.assertRaises(ValueError):
            _validate_search({"tenant_id": "not-a-uuid", "query": "meeting"})
        with self.assertRaises(ValueError):
            _validate_search({"tenant_id": TENANT, "query": "meeting", "tenant_override": TENANT})

    def test_internal_index_contract_is_strict_and_revision_is_positive(self):
        request = _validate_index({"tenant_id": TENANT, "transcript_id": TRANSCRIPT, "revision": 2})
        self.assertEqual(request["revision"], 2)
        with self.assertRaises(ValueError):
            _validate_index({"tenant_id": TENANT, "transcript_id": TRANSCRIPT, "revision": 0})
        with self.assertRaises(ValueError):
            _validate_index({"tenant_id": TENANT, "transcript_id": TRANSCRIPT, "caller": "untrusted"})

    def test_indexing_uses_authenticated_tenant_and_is_safe_to_retry(self):
        runtime = LocalHybridRetrievalRuntime.__new__(LocalHybridRetrievalRuntime)
        runtime.model = "local-e5"
        runtime.model_version = "sha256:model-revision"
        runtime.repository = FakeRepository()
        runtime.embeddings = FakeEmbeddings()
        runtime.semantic = FakeSemantic()
        from threading import RLock
        runtime._model_lock = RLock()

        first = runtime.index_transcript(TENANT, TRANSCRIPT, 2)
        second = runtime.index_transcript(TENANT, TRANSCRIPT, 2)
        self.assertEqual(first["indexed_segments"], 1)
        self.assertEqual(second["indexed_segments"], 1)
        self.assertEqual(runtime.repository.calls, [(TENANT, TRANSCRIPT, 2)] * 2)
        self.assertEqual(len(runtime.semantic.stored), 2)
        self.assertEqual(runtime.semantic.stored[0][0], TENANT)
        self.assertEqual(runtime.semantic.stored[0][1].text, "جلسه فروش فردا")

    def test_search_preserves_source_provenance_and_model_version(self):
        candidate = SearchCandidate(
            transcript_id=TRANSCRIPT, revision=2, asset_id=ASSET, segment_id=SEGMENT,
            start_ms=100, end_ms=900, text="جلسه فروش فردا", language="fa",
        )
        hit = SearchHit(candidate, 0.031, 1, 2)
        runtime = LocalHybridRetrievalRuntime.__new__(LocalHybridRetrievalRuntime)
        runtime.model = "local-e5"
        runtime.model_version = "sha256:model-revision"
        from threading import RLock
        runtime._model_lock = RLock()
        runtime.semantic = FakeSemantic(available=True)
        runtime.hybrid = FakeHybrid(hit)
        result = runtime.search(TENANT, {"query": "جلسه", "limit": 5, "language": "fa"})
        self.assertEqual(runtime.hybrid.call[0], "جلسه")
        self.assertEqual(runtime.hybrid.call[1].tenant_id, TENANT)
        self.assertEqual(runtime.hybrid.call[1].language, "fa")
        self.assertEqual(result["model_version"], "sha256:model-revision")
        self.assertEqual(result["items"][0]["segment_id"], SEGMENT)
        self.assertEqual(result["items"][0]["fused_score"], 0.031)

    def test_no_model_configuration_keeps_retrieval_disabled(self):
        values = {
            "VOXPIPE_EMBEDDING_MODEL": "",
            "VOXPIPE_EMBEDDING_MODEL_VERSION": "",
            "VOXPIPE_RETRIEVAL_DATABASE_DSN": "",
            "VOXPIPE_RETRIEVAL_INTERNAL_TOKEN": "",
        }
        with patch.dict(os.environ, values):
            self.assertIsNone(build_runtime())

    def test_internal_service_requires_shared_secret_and_fails_closed_without_model(self):
        previous_runtime = RetrievalHandler.runtime
        previous_token = RetrievalHandler.internal_token
        RetrievalHandler.runtime = None
        RetrievalHandler.internal_token = "s" * 32
        server = ThreadingHTTPServer(("127.0.0.1", 0), RetrievalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/internal/v1/search"
            body = json.dumps({"tenant_id": TENANT, "query": "meeting"}).encode()
            bad = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "X-VoxPipe-Retrieval-Token": "wrong",
            }, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(bad, timeout=3)
            self.assertEqual(denied.exception.code, 401)
            good = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "X-VoxPipe-Retrieval-Token": "s" * 32,
            }, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as disabled:
                urllib.request.urlopen(good, timeout=3)
            self.assertEqual(disabled.exception.code, 503)
            self.assertEqual(json.loads(disabled.exception.read())["code"], "RETRIEVAL_NOT_CONFIGURED")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            RetrievalHandler.runtime = previous_runtime
            RetrievalHandler.internal_token = previous_token

    def test_partial_model_configuration_fails_closed(self):
        with patch.dict(os.environ, {
            "VOXPIPE_EMBEDDING_MODEL": "local-e5",
            "VOXPIPE_EMBEDDING_MODEL_VERSION": "",
        }):
            with self.assertRaises(SystemExit):
                build_runtime()


    def test_search_fails_closed_when_no_matching_model_version_is_indexed(self):
        runtime = LocalHybridRetrievalRuntime.__new__(LocalHybridRetrievalRuntime)
        runtime.model = "local-e5"
        runtime.model_version = "sha256:model-revision"
        runtime.semantic = FakeSemantic(available=False)
        with self.assertRaises(RetrievalIndexNotReady):
            runtime.search(TENANT, {"query": "جلسه"})


    def test_model_configuration_requires_absolute_path_immutable_digest_and_384_dimensions(self):
        valid = {
            "VOXPIPE_EMBEDDING_MODEL": "local-e5",
            "VOXPIPE_EMBEDDING_MODEL_VERSION": "sha256:" + "a" * 64,
            "VOXPIPE_EMBEDDING_MODEL_PATH": "/preprovisioned/model",
            "VOXPIPE_EMBEDDING_DIMENSIONS": "384",
            "VOXPIPE_RETRIEVAL_DATABASE_DSN": "postgresql://localhost/voxpipe",
            "VOXPIPE_RETRIEVAL_INTERNAL_TOKEN": "s" * 32,
        }
        with patch.dict(os.environ, valid, clear=True):
            with patch("scripts.run_retrieval_service.LocalHybridRetrievalRuntime") as factory:
                self.assertIsNotNone(build_runtime())
                self.assertEqual(factory.call_args.kwargs["dimensions"], 384)
        for name, value in (
            ("VOXPIPE_EMBEDDING_MODEL_VERSION", "sha256:model-revision"),
            ("VOXPIPE_EMBEDDING_DIMENSIONS", "768"),
            ("VOXPIPE_EMBEDDING_MODEL_PATH", "relative/model"),
        ):
            invalid = dict(valid)
            invalid[name] = value
            with patch.dict(os.environ, invalid, clear=True):
                with self.assertRaises(SystemExit):
                    build_runtime()

    def test_internal_service_maps_missing_model_version_index_to_503(self):
        class NotReadyRuntime:
            def search(self, tenant_id, request):
                raise RetrievalIndexNotReady("index missing")

        previous_runtime = RetrievalHandler.runtime
        previous_token = RetrievalHandler.internal_token
        RetrievalHandler.runtime = NotReadyRuntime()
        RetrievalHandler.internal_token = "s" * 32
        server = ThreadingHTTPServer(("127.0.0.1", 0), RetrievalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/internal/v1/search"
            request = urllib.request.Request(url, data=json.dumps({
                "tenant_id": TENANT, "query": "جلسه",
            }).encode(), headers={
                "Content-Type": "application/json",
                "X-VoxPipe-Retrieval-Token": "s" * 32,
            }, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as unavailable:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(unavailable.exception.code, 503)
            self.assertEqual(json.loads(unavailable.exception.read())["code"], "RETRIEVAL_INDEX_NOT_READY")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            RetrievalHandler.runtime = previous_runtime
            RetrievalHandler.internal_token = previous_token


if __name__ == "__main__":
    unittest.main()
