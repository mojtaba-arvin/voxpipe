"""Provider-neutral object storage contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size_bytes: int
    sha256: str


class StorageError(Exception):
    code = "STORAGE_ERROR"


class InvalidObjectKeyError(StorageError):
    code = "STORAGE_INVALID_KEY"


class ObjectNotFoundError(StorageError):
    code = "STORAGE_NOT_FOUND"


class ObjectConflictError(StorageError):
    code = "STORAGE_CONFLICT"


class StorageIntegrityError(StorageError):
    code = "STORAGE_INTEGRITY_ERROR"


class StorageIOError(StorageError):
    code = "STORAGE_IO_ERROR"


def validate_object_key(key: str) -> tuple[str, ...]:
    """Validate an opaque relative POSIX object key shared by all adapters."""
    if not isinstance(key, str) or not key or len(key) > 1024:
        raise InvalidObjectKeyError("object key must contain 1 to 1024 characters")
    if "\x00" in key or "\\" in key or key.startswith("/"):
        raise InvalidObjectKeyError("object key must be a relative POSIX path")
    if ":" in key.split("/", 1)[0]:
        raise InvalidObjectKeyError("object key must not contain a drive prefix")
    if any(ord(character) < 32 or ord(character) == 127 for character in key):
        raise InvalidObjectKeyError("object key contains control characters")
    parts = tuple(key.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidObjectKeyError("object key contains an invalid path segment")
    return parts


class ObjectStoragePort(Protocol):
    """Opaque-key storage interface; provider SDK types never cross this port."""

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        expected_sha256: str | None = None,
        overwrite: bool = False,
    ) -> ObjectInfo:
        """Store a stream atomically and return its verified metadata."""
        ...

    def open(self, key: str) -> BinaryIO:
        """Open an object for binary reading; caller owns the returned handle."""
        ...

    def head(self, key: str) -> ObjectInfo:
        """Return size and content digest for the current object contents."""
        ...

    def delete(self, key: str) -> bool:
        """Delete an object; return False when it does not exist."""
        ...
