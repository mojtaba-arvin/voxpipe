"""Integrity, idempotency, and error-boundary tests for S3-compatible storage."""

from __future__ import annotations

import base64
import hashlib
import io
import unittest

from voxpipe.storage.s3 import S3CompatibleObjectStore
from voxpipe.storage.ports import (
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
    StorageIOError,
    StorageIntegrityError,
)


class FakeClientError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__("provider detail must never escape")
        self.response = {
            "Error": {"Code": code, "Message": "secret provider response"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class FailingStream:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return b"partial-content"
        raise OSError("simulated interrupted source")


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.omit_checksum = False
        self.fail_put_after_commit = False
        self.last_put: dict | None = None

    def put_object(self, **kwargs) -> dict:
        key = kwargs["Key"]
        body = kwargs["Body"].read()
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        self.last_put = kwargs
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise FakeClientError("PreconditionFailed", 412)
        self.objects[key] = {
            "body": body,
            "metadata": dict(kwargs["Metadata"]),
            "checksum": checksum,
            "encryption": kwargs["ServerSideEncryption"],
            "kms_key": kwargs.get("SSEKMSKeyId"),
        }
        if self.fail_put_after_commit:
            self.fail_put_after_commit = False
            raise TimeoutError("ambiguous response")
        return {"ChecksumSHA256": checksum}

    def head_object(self, *, Bucket: str, Key: str, ChecksumMode: str | None = None) -> dict:
        item = self.objects.get(Key)
        if item is None:
            raise FakeClientError("NoSuchKey", 404)
        result = {
            "ContentLength": len(item["body"]),
            "Metadata": item["metadata"],
            "ServerSideEncryption": item["encryption"],
        }
        if item["kms_key"]:
            result["SSEKMSKeyId"] = item["kms_key"]
        if not self.omit_checksum:
            result["ChecksumSHA256"] = item["checksum"]
        return result

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        item = self.objects.get(Key)
        if item is None:
            raise FakeClientError("NoSuchKey", 404)
        return {
            "Body": io.BytesIO(item["body"]),
            "ServerSideEncryption": item["encryption"],
            **({"SSEKMSKeyId": item["kms_key"]} if item["kms_key"] else {}),
        }

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self.objects.pop(Key, None)
        return {}


class S3CompatibleObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeS3Client()
        self.store = S3CompatibleObjectStore(self.client, bucket="voxpipe-test")

    def test_put_open_head_delete_round_trip(self) -> None:
        content = b"audio-object-content"
        info = self.store.put("tenant-a/assets/asset-1/source", io.BytesIO(content))
        self.assertEqual(info.size_bytes, len(content))
        self.assertEqual(info.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(self.client.last_put["IfNoneMatch"], "*")
        self.assertEqual(self.client.last_put["ServerSideEncryption"], "AES256")
        with self.store.open(info.key) as source:
            self.assertEqual(source.read(), content)
        self.assertEqual(self.store.head(info.key), info)
        self.assertTrue(self.store.delete(info.key))
        self.assertFalse(self.store.delete(info.key))

    def test_identical_put_is_idempotent(self) -> None:
        first = self.store.put("tenant-a/asset.wav", io.BytesIO(b"same"))
        second = self.store.put("tenant-a/asset.wav", io.BytesIO(b"same"))
        self.assertEqual(first, second)

    def test_different_content_conflicts_without_overwrite(self) -> None:
        self.store.put("tenant-a/asset.wav", io.BytesIO(b"original"))
        with self.assertRaises(ObjectConflictError):
            self.store.put("tenant-a/asset.wav", io.BytesIO(b"replacement"))
        self.assertEqual(self.client.objects["tenant-a/asset.wav"]["body"], b"original")

    def test_explicit_overwrite_is_deliberate(self) -> None:
        self.store.put("tenant-a/asset.wav", io.BytesIO(b"old"))
        self.store.put("tenant-a/asset.wav", io.BytesIO(b"new"), overwrite=True)
        self.assertEqual(self.client.objects["tenant-a/asset.wav"]["body"], b"new")
        self.assertNotIn("IfNoneMatch", self.client.last_put)

    def test_ambiguous_put_response_reconciles_by_checksum(self) -> None:
        self.client.fail_put_after_commit = True
        info = self.store.put("tenant-a/asset.wav", io.BytesIO(b"committed"))
        self.assertEqual(info.sha256, hashlib.sha256(b"committed").hexdigest())

    def test_interrupted_source_never_starts_provider_upload(self) -> None:
        with self.assertRaises(StorageIOError):
            self.store.put("tenant-a/interrupted.wav", FailingStream())
        self.assertNotIn("tenant-a/interrupted.wav", self.client.objects)

    def test_expected_checksum_mismatch_leaves_no_object(self) -> None:
        with self.assertRaises(StorageIntegrityError):
            self.store.put("tenant-a/bad.wav", io.BytesIO(b"content"), expected_sha256="0" * 64)
        self.assertNotIn("tenant-a/bad.wav", self.client.objects)

    def test_oversized_object_leaves_no_object(self) -> None:
        store = S3CompatibleObjectStore(self.client, bucket="voxpipe-test", max_object_size_bytes=4)
        with self.assertRaises(StorageIntegrityError):
            store.put("tenant-a/large.wav", io.BytesIO(b"12345"))
        self.assertNotIn("tenant-a/large.wav", self.client.objects)

    def test_invalid_object_keys_are_rejected(self) -> None:
        for key in ("../escape", "/absolute", "tenant//empty", "tenant/./dot", "tenant\\escape", "tenant/with\x00null"):
            with self.subTest(key=key):
                with self.assertRaises(InvalidObjectKeyError):
                    self.store.put(key, io.BytesIO(b"x"))

    def test_head_falls_back_to_streamed_digest_when_provider_omits_checksum(self) -> None:
        info = self.store.put("tenant-a/asset.wav", io.BytesIO(b"verified"))
        self.client.omit_checksum = True
        self.assertEqual(self.store.head(info.key), info)

    def test_head_retries_without_checksum_mode_for_compatible_endpoint(self) -> None:
        class NoChecksumModeClient(FakeS3Client):
            def head_object(self, *, Bucket: str, Key: str, ChecksumMode: str | None = None) -> dict:
                if ChecksumMode:
                    raise FakeClientError("NotImplemented", 501)
                return super().head_object(Bucket=Bucket, Key=Key)

        client = NoChecksumModeClient()
        client.omit_checksum = True
        store = S3CompatibleObjectStore(client, bucket="voxpipe-test")
        info = store.put("tenant-a/legacy.wav", io.BytesIO(b"legacy-compatible"))
        self.assertEqual(store.head(info.key), info)

    def test_head_rejects_mismatched_provider_checksum_and_metadata(self) -> None:
        self.store.put("tenant-a/asset.wav", io.BytesIO(b"verified"))
        self.client.objects["tenant-a/asset.wav"]["metadata"]["voxpipe-sha256"] = "0" * 64
        with self.assertRaises(StorageIntegrityError):
            self.store.head("tenant-a/asset.wav")

    def test_head_fails_closed_when_encryption_is_not_applied(self) -> None:
        self.store.put("tenant-a/asset.wav", io.BytesIO(b"verified"))
        self.client.objects["tenant-a/asset.wav"]["encryption"] = None
        with self.assertRaises(StorageIntegrityError):
            self.store.head("tenant-a/asset.wav")

    def test_kms_requires_key_and_verifies_kms_metadata(self) -> None:
        with self.assertRaises(ValueError):
            S3CompatibleObjectStore(self.client, bucket="voxpipe-test", server_side_encryption="aws:kms")
        store = S3CompatibleObjectStore(
            self.client,
            bucket="voxpipe-test",
            server_side_encryption="aws:kms",
            kms_key_id="key-id-123",
        )
        info = store.put("tenant-a/kms.wav", io.BytesIO(b"encrypted"))
        self.assertEqual(store.head(info.key).sha256, info.sha256)
        self.assertEqual(self.client.last_put["SSEKMSKeyId"], "key-id-123")

    def test_provider_error_is_sanitized(self) -> None:
        class BrokenClient(FakeS3Client):
            def get_object(self, **kwargs):
                raise FakeClientError("AccessDenied", 403)

        store = S3CompatibleObjectStore(BrokenClient(), bucket="voxpipe-test")
        with self.assertRaises(StorageIOError) as caught:
            store.open("tenant-a/private.wav")
        self.assertNotIn("secret provider response", str(caught.exception))
        self.assertNotIn("provider detail", str(caught.exception))

    def test_missing_object_maps_to_not_found(self) -> None:
        with self.assertRaises(ObjectNotFoundError):
            self.store.open("tenant-a/missing.wav")


if __name__ == "__main__":
    unittest.main()
