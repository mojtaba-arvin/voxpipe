"""Object storage ports and adapters."""

from voxpipe.storage.factory import build_object_store
from voxpipe.storage.local import LocalFilesystemObjectStore
from voxpipe.storage.s3 import S3CompatibleObjectStore
from voxpipe.storage.ports import (
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStoragePort,
    StorageError,
    StorageIOError,
    StorageIntegrityError,
    validate_object_key,
)

__all__ = [
    "InvalidObjectKeyError",
    "LocalFilesystemObjectStore",
    "S3CompatibleObjectStore",
    "build_object_store",
    "validate_object_key",
    "ObjectConflictError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStoragePort",
    "StorageError",
    "StorageIOError",
    "StorageIntegrityError",
]
