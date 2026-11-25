"""Disposable S3-compatible endpoint integration test; never targets a live cloud."""

from __future__ import annotations

import io
import os
import unittest
from uuid import uuid4

import boto3

from voxpipe.storage.factory import build_object_store
from voxpipe.storage.ports import ObjectConflictError, ObjectNotFoundError


@unittest.skipUnless(os.environ.get("VOXPIPE_S3_ENDPOINT_URL"), "disposable S3 endpoint is not configured")
class S3CompatibleObjectStoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env = dict(os.environ)
        cls.bucket = cls.env["VOXPIPE_S3_BUCKET"]
        cls.client = boto3.client(
            "s3",
            endpoint_url=cls.env["VOXPIPE_S3_ENDPOINT_URL"],
            region_name=cls.env["VOXPIPE_S3_REGION"],
            aws_access_key_id=cls.env["VOXPIPE_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=cls.env["VOXPIPE_S3_SECRET_ACCESS_KEY"],
        )
        cls.client.create_bucket(Bucket=cls.bucket)
        cls.first, first_provider = build_object_store(
            cls.env,
            local_root=None,
            local_root_env="VOXPIPE_OBJECT_ROOT",
            max_object_size_bytes=512 * 1024 * 1024,
        )
        cls.second, second_provider = build_object_store(
            cls.env,
            local_root=None,
            local_root_env="VOXPIPE_OBJECT_ROOT",
            max_object_size_bytes=512 * 1024 * 1024,
        )
        if first_provider != "s3_compatible" or second_provider != "s3_compatible":
            raise AssertionError("integration test must select S3-compatible storage")

    @classmethod
    def tearDownClass(cls) -> None:
        response = cls.client.list_objects_v2(Bucket=cls.bucket)
        for item in response.get("Contents", []):
            cls.client.delete_object(Bucket=cls.bucket, Key=item["Key"])
        cls.client.delete_bucket(Bucket=cls.bucket)

    def test_independent_clients_share_verified_tenant_object_lifecycle(self) -> None:
        tenant = str(uuid4())
        key = f"{tenant}/assets/{uuid4()}/source"
        content = b"voxpipe-s3-integration-audio"
        first_info = self.first.put(key, io.BytesIO(content))
        second_info = self.second.head(key)
        self.assertEqual(first_info, second_info)

        with self.second.open(key) as source:
            self.assertEqual(source.read(), content)

        same = self.second.put(key, io.BytesIO(content))
        self.assertEqual(same, first_info)
        with self.assertRaises(ObjectConflictError):
            self.second.put(key, io.BytesIO(b"different-content"))

        self.assertTrue(self.first.delete(key))
        with self.assertRaises(ObjectNotFoundError):
            self.second.head(key)
        self.assertFalse(self.second.delete(key))


if __name__ == "__main__":
    unittest.main()
