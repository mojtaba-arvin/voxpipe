"""S3-compatible implementation of the provider-neutral object storage port."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import tempfile
from typing import Any, BinaryIO

from voxpipe.storage.ports import (
    ObjectConflictError,
    ObjectInfo,
    ObjectNotFoundError,
    StorageIOError,
    StorageIntegrityError,
    validate_object_key,
)


_CHUNK_SIZE = 1024 * 1024
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_SINGLE_PUT_LIMIT = 5 * 1024 * 1024 * 1024


class S3CompatibleObjectStore:
    """S3 API adapter with immutable conditional writes and verified checksums.

    The adapter uses one PutObject request for each object. Its configured size
    limit must not exceed S3's 5 GiB single-request limit. Multipart upload is
    deliberately excluded until a larger object limit is justified.
    """

    def __init__(
        self,
        client: Any,
        *,
        bucket: str,
        max_object_size_bytes: int = 512 * 1024 * 1024,
        server_side_encryption: str = "AES256",
        kms_key_id: str | None = None,
    ) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("S3 bucket is required")
        if not 0 < max_object_size_bytes <= _SINGLE_PUT_LIMIT:
            raise ValueError("max_object_size_bytes must be between 1 byte and 5 GiB")
        if server_side_encryption not in {"AES256", "aws:kms"}:
            raise ValueError("server_side_encryption must be AES256 or aws:kms")
        if server_side_encryption == "aws:kms" and not (kms_key_id and kms_key_id.strip()):
            raise ValueError("kms_key_id is required when aws:kms encryption is selected")
        if server_side_encryption == "AES256" and kms_key_id:
            raise ValueError("kms_key_id is only valid with aws:kms encryption")
        self._client = client
        self._bucket = bucket.strip()
        self._max_object_size_bytes = max_object_size_bytes
        self._server_side_encryption = server_side_encryption
        self._kms_key_id = kms_key_id.strip() if kms_key_id else None

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        expected_sha256: str | None = None,
        overwrite: bool = False,
    ) -> ObjectInfo:
        validate_object_key(key)
        if expected_sha256 is not None and not _SHA256_RE.fullmatch(expected_sha256):
            raise StorageIntegrityError("expected_sha256 must be a lowercase SHA-256 digest")

        digest = hashlib.sha256()
        size_bytes = 0
        with tempfile.TemporaryFile(mode="w+b") as staged:
            try:
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
                    staged.write(chunk)
                staged.flush()
                staged.seek(0)
            except (StorageIOError, StorageIntegrityError):
                raise
            except Exception as exc:
                raise StorageIOError("object source stream could not be read") from exc

            checksum = digest.hexdigest()
            if expected_sha256 is not None and not hmac.compare_digest(checksum, expected_sha256):
                raise StorageIntegrityError("object checksum does not match expected_sha256")

            params: dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": key,
                "Body": staged,
                "ContentLength": size_bytes,
                "ChecksumSHA256": base64.b64encode(digest.digest()).decode("ascii"),
                "Metadata": {"voxpipe-sha256": checksum},
                "ServerSideEncryption": self._server_side_encryption,
            }
            if self._kms_key_id:
                params["SSEKMSKeyId"] = self._kms_key_id
            if not overwrite:
                params["IfNoneMatch"] = "*"

            try:
                self._client.put_object(**params)
            except Exception as exc:
                if not overwrite and self._is_precondition_failure(exc):
                    return self._existing_or_conflict(key, size_bytes, checksum)
                # A timeout can occur after the provider committed the object.
                # Reconcile by content before surfacing an ambiguous write.
                try:
                    existing = self.head(key)
                except ObjectNotFoundError:
                    raise StorageIOError("object could not be stored by the S3-compatible provider") from None
                if existing.size_bytes == size_bytes and hmac.compare_digest(existing.sha256, checksum):
                    return existing
                if not overwrite:
                    raise ObjectConflictError("object key already exists with different content") from None
                raise StorageIOError("object overwrite could not be confirmed by the S3-compatible provider") from None

        info = self.head(key)
        if info.size_bytes != size_bytes or not hmac.compare_digest(info.sha256, checksum):
            raise StorageIntegrityError("stored object does not match the uploaded content")
        return info

    def open(self, key: str) -> BinaryIO:
        validate_object_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            self._raise_provider_error(exc, "object could not be opened")
        self._verify_encryption(response)
        return response["Body"]

    def head(self, key: str) -> ObjectInfo:
        validate_object_key(key)
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
            )
        except Exception as exc:
            code, status = self._error_details(exc)
            if code in {"InvalidArgument", "NotImplemented", "501"} or status == 501:
                try:
                    response = self._client.head_object(Bucket=self._bucket, Key=key)
                except Exception as fallback_exc:
                    self._raise_provider_error(fallback_exc, "object metadata could not be read")
            else:
                self._raise_provider_error(exc, "object metadata could not be read")

        try:
            size_bytes = int(response.get("ContentLength", -1))
        except (TypeError, ValueError):
            raise StorageIntegrityError("provider returned an invalid object size") from None
        if size_bytes < 0 or size_bytes > self._max_object_size_bytes:
            raise StorageIntegrityError("stored object size is outside the configured limit")
        self._verify_encryption(response)

        metadata = response.get("Metadata") or {}
        metadata_digest = metadata.get("voxpipe-sha256")
        provider_checksum = response.get("ChecksumSHA256")
        if provider_checksum:
            try:
                checksum = base64.b64decode(provider_checksum, validate=True).hex()
            except (ValueError, TypeError):
                raise StorageIntegrityError("provider returned an invalid SHA-256 checksum") from None
            if not _SHA256_RE.fullmatch(checksum):
                raise StorageIntegrityError("provider returned an invalid SHA-256 checksum")
            if metadata_digest and not hmac.compare_digest(metadata_digest, checksum):
                raise StorageIntegrityError("stored checksum metadata does not match provider checksum")
        else:
            # Some S3-compatible implementations omit checksum headers. Do not
            # trust user metadata alone: stream the object and calculate SHA-256.
            checksum, downloaded_size = self._download_digest(key)
            if downloaded_size != size_bytes:
                raise StorageIntegrityError("downloaded object size does not match provider metadata")
            if metadata_digest and not hmac.compare_digest(metadata_digest, checksum):
                raise StorageIntegrityError("stored checksum metadata does not match object content")

        return ObjectInfo(key=key, size_bytes=size_bytes, sha256=checksum)

    def delete(self, key: str) -> bool:
        validate_object_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            self._raise_provider_error(exc, "object existence could not be checked")
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:
            self._raise_provider_error(exc, "object could not be deleted")

    def _existing_or_conflict(self, key: str, size_bytes: int, checksum: str) -> ObjectInfo:
        existing = self.head(key)
        if existing.size_bytes == size_bytes and hmac.compare_digest(existing.sha256, checksum):
            return existing
        raise ObjectConflictError("object key already exists with different content")

    def _download_digest(self, key: str) -> tuple[str, int]:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            digest = hashlib.sha256()
            size_bytes = 0
            try:
                while True:
                    chunk = body.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise StorageIOError("provider returned a non-binary object stream")
                    size_bytes += len(chunk)
                    digest.update(chunk)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            return digest.hexdigest(), size_bytes
        except StorageIOError:
            raise
        except Exception as exc:
            self._raise_provider_error(exc, "object checksum could not be verified")

    def _verify_encryption(self, response: dict[str, Any]) -> None:
        actual = response.get("ServerSideEncryption")
        if actual != self._server_side_encryption:
            raise StorageIntegrityError("stored object does not satisfy the configured encryption requirement")
        if self._server_side_encryption == "aws:kms" and not response.get("SSEKMSKeyId"):
            raise StorageIntegrityError("stored object is missing its required KMS key metadata")

    @staticmethod
    def _error_details(exc: Exception) -> tuple[str, int | None]:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return "", None
        error = response.get("Error") or {}
        metadata = response.get("ResponseMetadata") or {}
        return str(error.get("Code", "")), metadata.get("HTTPStatusCode")

    @classmethod
    def _is_not_found(cls, exc: Exception) -> bool:
        code, status = cls._error_details(exc)
        return code in {"NoSuchKey", "NotFound", "404"} or status == 404

    @classmethod
    def _is_precondition_failure(cls, exc: Exception) -> bool:
        code, status = cls._error_details(exc)
        return code in {"PreconditionFailed", "ConditionalRequestConflict", "412", "409"} or status in {409, 412}

    @classmethod
    def _raise_provider_error(cls, exc: Exception, message: str) -> None:
        if cls._is_not_found(exc):
            raise ObjectNotFoundError("object does not exist") from None
        raise StorageIOError(message) from None
