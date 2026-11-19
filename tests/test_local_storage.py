"""Security, integrity, and atomicity tests for local object storage."""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from voxpipe.storage import (
    InvalidObjectKeyError,
    LocalFilesystemObjectStore,
    ObjectConflictError,
    ObjectNotFoundError,
    StorageIOError,
    StorageIntegrityError,
)


class FailingStream:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return b"partial-content"
        raise OSError("simulated interrupted source")


class LocalFilesystemObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "objects"
        self.store = LocalFilesystemObjectStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_put_open_head_delete_round_trip(self) -> None:
        content = b"audio-object-content"
        expected = hashlib.sha256(content).hexdigest()
        info = self.store.put(
            "tenant-a/assets/asset-1/source",
            io.BytesIO(content),
            expected_sha256=expected,
        )
        self.assertEqual(info.size_bytes, len(content))
        self.assertEqual(info.sha256, expected)
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
        with self.store.open("tenant-a/asset.wav") as source:
            self.assertEqual(source.read(), b"original")

    def test_explicit_overwrite_is_atomic_and_replaces_content(self) -> None:
        self.store.put("tenant-a/asset.wav", io.BytesIO(b"old"))
        updated = self.store.put(
            "tenant-a/asset.wav",
            io.BytesIO(b"new"),
            overwrite=True,
        )
        self.assertEqual(updated.sha256, hashlib.sha256(b"new").hexdigest())
        with self.store.open("tenant-a/asset.wav") as source:
            self.assertEqual(source.read(), b"new")

    def test_expected_checksum_mismatch_leaves_no_object(self) -> None:
        with self.assertRaises(StorageIntegrityError):
            self.store.put(
                "tenant-a/bad.wav",
                io.BytesIO(b"content"),
                expected_sha256="0" * 64,
            )
        with self.assertRaises(ObjectNotFoundError):
            self.store.head("tenant-a/bad.wav")
        self.assertEqual(list(self.root.rglob(".voxpipe-tmp-*")), [])

    def test_oversized_object_leaves_no_partial_file(self) -> None:
        store = LocalFilesystemObjectStore(self.root, max_object_size_bytes=4)
        with self.assertRaises(StorageIntegrityError):
            store.put("tenant-a/large.wav", io.BytesIO(b"12345"))
        self.assertFalse((self.root / "tenant-a" / "large.wav").exists())
        self.assertEqual(list(self.root.rglob(".voxpipe-tmp-*")), [])

    def test_invalid_keys_are_rejected(self) -> None:
        invalid_keys = [
            "../escape",
            "tenant/../escape",
            "/absolute/path",
            "tenant//empty-segment",
            "tenant/./dot-segment",
            "C:/drive-prefix",
            "tenant\\escape",
            "tenant/with\x00null",
        ]
        for key in invalid_keys:
            with self.subTest(key=key):
                with self.assertRaises(InvalidObjectKeyError):
                    self.store.put(key, io.BytesIO(b"x"))

    def test_symlink_parent_cannot_escape_storage_root(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        link = self.root / "tenant-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symbolic links are unavailable")
        with self.assertRaises(InvalidObjectKeyError):
            self.store.put("tenant-link/escaped.bin", io.BytesIO(b"secret"))
        self.assertFalse((outside / "escaped.bin").exists())

    def test_symlink_object_target_is_rejected(self) -> None:
        outside = Path(self.temporary.name) / "outside.bin"
        outside.write_bytes(b"do-not-touch")
        link = self.root / "tenant-a" / "linked.bin"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links are unavailable")
        with self.assertRaises(InvalidObjectKeyError):
            self.store.put("tenant-a/linked.bin", io.BytesIO(b"replacement"))
        self.assertEqual(outside.read_bytes(), b"do-not-touch")

    def test_interrupted_source_cleans_temporary_file(self) -> None:
        with self.assertRaises(StorageIOError):
            self.store.put("tenant-a/interrupted.bin", FailingStream())  # type: ignore[arg-type]
        self.assertFalse((self.root / "tenant-a" / "interrupted.bin").exists())
        self.assertEqual(list(self.root.rglob(".voxpipe-tmp-*")), [])

    def test_missing_object_operations_are_explicit(self) -> None:
        with self.assertRaises(ObjectNotFoundError):
            self.store.open("tenant-a/missing")
        with self.assertRaises(ObjectNotFoundError):
            self.store.head("tenant-a/missing")
        self.assertFalse(self.store.delete("tenant-a/missing"))

    def test_head_detects_content_changes(self) -> None:
        info = self.store.put("tenant-a/asset.wav", io.BytesIO(b"original"))
        target = self.root / info.key
        target.write_bytes(b"tampered")
        current = self.store.head(info.key)
        self.assertNotEqual(current.sha256, info.sha256)


if __name__ == "__main__":
    unittest.main()
