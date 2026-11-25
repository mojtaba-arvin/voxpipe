"""Storage backend factory configuration and fail-closed behavior tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from voxpipe.storage.factory import build_object_store
from voxpipe.storage.local import LocalFilesystemObjectStore


class StorageFactoryTests(unittest.TestCase):
    def test_defaults_to_local_storage_without_cloud_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, provider = build_object_store(
                {},
                local_root=Path(temporary) / "objects",
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )
        self.assertIsInstance(store, LocalFilesystemObjectStore)
        self.assertEqual(provider, "local")

    def test_local_backend_requires_its_configured_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "VOXPIPE_OBJECT_ROOT"):
            build_object_store(
                {},
                local_root=None,
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )

    def test_unknown_backend_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be local or s3_compatible"):
            build_object_store(
                {"VOXPIPE_STORAGE_BACKEND": "unknown"},
                local_root="/tmp/unused",
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )

    def test_s3_requires_explicit_bucket_region_and_encryption(self) -> None:
        with self.assertRaisesRegex(ValueError, "VOXPIPE_S3_BUCKET and VOXPIPE_S3_REGION"):
            build_object_store(
                {"VOXPIPE_STORAGE_BACKEND": "s3_compatible"},
                local_root=None,
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )
        with self.assertRaisesRegex(ValueError, "SERVER_SIDE_ENCRYPTION"):
            build_object_store(
                {
                    "VOXPIPE_STORAGE_BACKEND": "s3_compatible",
                    "VOXPIPE_S3_BUCKET": "voxpipe",
                    "VOXPIPE_S3_REGION": "us-east-1",
                },
                local_root=None,
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )

    def test_s3_kms_requires_key_and_credentials_must_be_paired(self) -> None:
        base = {
            "VOXPIPE_STORAGE_BACKEND": "s3_compatible",
            "VOXPIPE_S3_BUCKET": "voxpipe",
            "VOXPIPE_S3_REGION": "us-east-1",
            "VOXPIPE_S3_SERVER_SIDE_ENCRYPTION": "aws:kms",
        }
        with self.assertRaisesRegex(ValueError, "KMS_KEY_ID"):
            build_object_store(base, local_root=None, local_root_env="VOXPIPE_OBJECT_ROOT", max_object_size_bytes=1024)
        base["VOXPIPE_S3_KMS_KEY_ID"] = "alias/voxpipe"
        base["VOXPIPE_S3_ACCESS_KEY_ID"] = "only-half"
        with self.assertRaisesRegex(ValueError, "must be configured together"):
            build_object_store(base, local_root=None, local_root_env="VOXPIPE_OBJECT_ROOT", max_object_size_bytes=1024)

    def test_endpoint_paths_and_query_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "without paths"):
            build_object_store(
                {
                    "VOXPIPE_STORAGE_BACKEND": "s3_compatible",
                    "VOXPIPE_S3_BUCKET": "voxpipe",
                    "VOXPIPE_S3_REGION": "us-east-1",
                    "VOXPIPE_S3_SERVER_SIDE_ENCRYPTION": "AES256",
                    "VOXPIPE_S3_ENDPOINT_URL": "http://localhost:9000/prefix",
                },
                local_root=None,
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )

    def test_remote_http_endpoint_is_rejected_before_sdk_initialization(self) -> None:
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            build_object_store(
                {
                    "VOXPIPE_STORAGE_BACKEND": "s3_compatible",
                    "VOXPIPE_S3_BUCKET": "voxpipe",
                    "VOXPIPE_S3_REGION": "us-east-1",
                    "VOXPIPE_S3_SERVER_SIDE_ENCRYPTION": "AES256",
                    "VOXPIPE_S3_ENDPOINT_URL": "http://objects.example.com",
                },
                local_root=None,
                local_root_env="VOXPIPE_OBJECT_ROOT",
                max_object_size_bytes=1024,
            )


if __name__ == "__main__":
    unittest.main()
