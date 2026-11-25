from __future__ import annotations
import hashlib, json, tempfile, unittest
from pathlib import Path
from scripts.voxpipe_cli import APIClient, APIError, ingest, sha256_file

class FakeResponse:
    def __init__(self, payload=b"{}"): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size=-1): return self.payload[:size]

class FakeOpener:
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)

class VoxPipeCLITests(unittest.TestCase):
    def test_client_requires_tls_for_remote_api_and_token(self):
        with self.assertRaises(ValueError): APIClient("http://api.example.com", "token")
        with self.assertRaises(ValueError): APIClient("https://api.example.com", "")

    def test_hash_file_streams_and_returns_exact_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"; path.write_bytes(b"voxpipe-audio")
            size, digest = sha256_file(path)
        self.assertEqual(size, 13)
        self.assertEqual(digest, hashlib.sha256(b"voxpipe-audio").hexdigest())

    def test_ingest_executes_create_upload_finalize_in_order(self):
        session = "11111111-1111-4111-8111-111111111111"
        client = APIClient("http://127.0.0.1:8080", "secret")
        opener = FakeOpener([FakeResponse(json.dumps({"session_id": session}).encode()), FakeResponse(),
                             FakeResponse(json.dumps({"job_id":"job-1","state":"queued"}).encode())])
        client.opener = opener
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"; path.write_bytes(b"audio bytes")
            result = ingest(client, path, "file_upload", None, False, 1, 10, 60)
        self.assertEqual(result["session_id"], session)
        self.assertEqual([item[0].method for item in opener.requests], ["POST","PUT","POST"])
        self.assertEqual(len(opener.requests[0][0].get_header("Idempotency-key")), 32)
        self.assertEqual(opener.requests[1][0].get_header("Content-type"), "application/octet-stream")
        self.assertEqual(opener.requests[1][0].get_header("Content-length"), "11")
        self.assertEqual(opener.requests[1][1], 60)
        self.assertTrue(hasattr(opener.requests[1][0].data, "read"))\n        self.assertTrue(opener.requests[1][0].data.closed)
        self.assertEqual(opener.requests[2][0].full_url, f"http://127.0.0.1:8080/v1/ingestions/{session}/finalize")

    def test_ingest_rejects_empty_file_before_network(self):
        client = APIClient("http://127.0.0.1:8080", "secret")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.wav"; path.write_bytes(b"")
            with self.assertRaises(ValueError): ingest(client, path, "file_upload", None, False, 1, 10)

    def test_response_size_is_bounded(self):
        client = APIClient("http://127.0.0.1:8080", "secret")
        client.opener = FakeOpener([FakeResponse(b"x" * 1_048_577)])
        with self.assertRaises(APIError) as caught:
            client.request("GET", "/v1/ingestions/11111111-1111-4111-8111-111111111111")
        self.assertEqual(caught.exception.code, "RESPONSE_TOO_LARGE")

if __name__ == "__main__": unittest.main()
