"""Local filesystem implementation of the provider-neutral storage port."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO

from voxpipe.storage.ports import (
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectInfo,
    ObjectNotFoundError,
    StorageIOError,
    StorageIntegrityError,
    validate_object_key,
)


_CHUNK_SIZE = 1024 * 1024
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class LocalFilesystemObjectStore:
    """Atomic local object store for development and single-node deployments.

    The root directory must be owned by the service account and must not be
    writable by untrusted users. Production multi-node deployments should use
    a dedicated object-storage adapter.
    """

    def __init__(self, root: str | Path, *, max_object_size_bytes: int = 512 * 1024 * 1024) -> None:
        if max_object_size_bytes <= 0:
            raise ValueError("max_object_size_bytes must be greater than zero")
        self._root = Path(root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)
        self._root = self._root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("object store root must be a directory")
        self._max_object_size_bytes = max_object_size_bytes

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        expected_sha256: str | None = None,
        overwrite: bool = False,
    ) -> ObjectInfo:
        if expected_sha256 is not None and not _SHA256_RE.fullmatch(expected_sha256):
            raise StorageIntegrityError("expected_sha256 must be a lowercase SHA-256 digest")

        target = self._target(key, create_parents=True)
        if target.exists() and not target.is_file():
            raise ObjectConflictError("object key already refers to a non-file entry")

        temp_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".voxpipe-tmp-",
                dir=target.parent,
            )
            temp_path = Path(temporary_name)
            digest = hashlib.sha256()
            size_bytes = 0

            with os.fdopen(descriptor, "wb") as destination:
                while True:
                    chunk = source.read(_CHUNK_SIZE)
                    if chunk == b"":
                        break
                    if not isinstance(chunk, bytes):
                        raise StorageIOError("source stream must return bytes")
                    size_bytes += len(chunk)
                    if size_bytes > self._max_object_size_bytes:
                        raise StorageIntegrityError("object exceeds the configured size limit")
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())

            checksum = digest.hexdigest()
            if expected_sha256 is not None and not hmac.compare_digest(checksum, expected_sha256):
                raise StorageIntegrityError("object checksum does not match expected_sha256")

            if target.is_symlink():
                raise InvalidObjectKeyError("symbolic-link object targets are not accepted")

            if overwrite:
                os.replace(temp_path, target)
                temp_path = None
            else:
                try:
                    # Same-directory hard link is an atomic create-if-absent.
                    os.link(temp_path, target)
                except FileExistsError:
                    existing = self.head(key)
                    if existing.size_bytes == size_bytes and hmac.compare_digest(existing.sha256, checksum):
                        return existing
                    raise ObjectConflictError("object key already exists with different content") from None
                else:
                    temp_path.unlink()
                    temp_path = None

            self._fsync_directory(target.parent)
            return ObjectInfo(key=key, size_bytes=size_bytes, sha256=checksum)
        except (StorageIntegrityError, InvalidObjectKeyError, ObjectConflictError):
            raise
        except OSError as exc:
            raise StorageIOError("object could not be written atomically") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def open(self, key: str) -> BinaryIO:
        target = self._target(key, create_parents=False)
        if not target.exists():
            raise ObjectNotFoundError("object does not exist")
        if not target.is_file():
            raise ObjectNotFoundError("object does not exist")
        try:
            return target.open("rb")
        except OSError as exc:
            raise StorageIOError("object could not be opened") from exc

    def head(self, key: str) -> ObjectInfo:
        target = self._target(key, create_parents=False)
        if not target.exists() or not target.is_file():
            raise ObjectNotFoundError("object does not exist")
        digest = hashlib.sha256()
        try:
            size_bytes = 0
            with target.open("rb") as source:
                while chunk := source.read(_CHUNK_SIZE):
                    size_bytes += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise StorageIOError("object metadata could not be read") from exc
        return ObjectInfo(key=key, size_bytes=size_bytes, sha256=digest.hexdigest())

    def delete(self, key: str) -> bool:
        target = self._target(key, create_parents=False)
        if not target.exists():
            return False
        if not target.is_file():
            raise ObjectNotFoundError("object does not exist")
        try:
            target.unlink()
            self._fsync_directory(target.parent)
            return True
        except OSError as exc:
            raise StorageIOError("object could not be deleted") from exc

    def _target(self, key: str, *, create_parents: bool) -> Path:
        parts = self._validate_key(key)
        current = self._root
        for part in parts[:-1]:
            candidate = current / part
            if candidate.is_symlink():
                raise InvalidObjectKeyError("symbolic-link directories are not accepted")
            if create_parents:
                candidate.mkdir(exist_ok=True)
            if candidate.exists():
                if not candidate.is_dir():
                    raise InvalidObjectKeyError("object key parent is not a directory")
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(self._root):
                    raise InvalidObjectKeyError("object key escapes the storage root")
            current = candidate

        target = current / parts[-1]
        if target.is_symlink():
            raise InvalidObjectKeyError("symbolic-link object targets are not accepted")
        return target

    @staticmethod
    def _validate_key(key: str) -> tuple[str, ...]:
        return validate_object_key(key)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
