"""Environment-driven object storage selection with explicit fail-closed S3 setup."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from voxpipe.storage.local import LocalFilesystemObjectStore
from voxpipe.storage.ports import ObjectStoragePort
from voxpipe.storage.s3 import S3CompatibleObjectStore


def _positive_float(env: Mapping[str, str], key: str, default: float, maximum: float) -> float:
    raw = env.get(key, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{key} must be greater than zero and at most {maximum}")
    return value


def build_object_store(
    env: Mapping[str, str],
    *,
    local_root: str | Path | None,
    local_root_env: str,
    max_object_size_bytes: int,
) -> tuple[ObjectStoragePort, str]:
    """Build the configured store; S3 selection never falls back to local."""
    backend = env.get("VOXPIPE_STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        if local_root is None or not str(local_root).strip():
            raise ValueError(f"{local_root_env} is required when VOXPIPE_STORAGE_BACKEND=local")
        return (
            LocalFilesystemObjectStore(
                local_root,
                max_object_size_bytes=max_object_size_bytes,
            ),
            "local",
        )
    if backend != "s3_compatible":
        raise ValueError("VOXPIPE_STORAGE_BACKEND must be local or s3_compatible")

    bucket = env.get("VOXPIPE_S3_BUCKET", "").strip()
    region = env.get("VOXPIPE_S3_REGION", "").strip()
    endpoint_url = env.get("VOXPIPE_S3_ENDPOINT_URL", "").strip() or None
    access_key = env.get("VOXPIPE_S3_ACCESS_KEY_ID", "").strip()
    secret_key = env.get("VOXPIPE_S3_SECRET_ACCESS_KEY", "").strip()
    encryption = env.get("VOXPIPE_S3_SERVER_SIDE_ENCRYPTION", "").strip()
    kms_key_id = env.get("VOXPIPE_S3_KMS_KEY_ID", "").strip() or None
    addressing_style = env.get("VOXPIPE_S3_ADDRESSING_STYLE", "path").strip().lower()

    if not bucket or not region:
        raise ValueError("VOXPIPE_S3_BUCKET and VOXPIPE_S3_REGION are required for S3-compatible storage")
    if bool(access_key) != bool(secret_key):
        raise ValueError("VOXPIPE_S3_ACCESS_KEY_ID and VOXPIPE_S3_SECRET_ACCESS_KEY must be configured together")
    if encryption not in {"AES256", "aws:kms"}:
        raise ValueError("VOXPIPE_S3_SERVER_SIDE_ENCRYPTION must explicitly be AES256 or aws:kms")
    if encryption == "aws:kms" and not kms_key_id:
        raise ValueError("VOXPIPE_S3_KMS_KEY_ID is required when aws:kms encryption is selected")
    if encryption == "AES256" and kms_key_id:
        raise ValueError("VOXPIPE_S3_KMS_KEY_ID is only valid with aws:kms encryption")
    if addressing_style not in {"path", "virtual"}:
        raise ValueError("VOXPIPE_S3_ADDRESSING_STYLE must be path or virtual")

    if endpoint_url:
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("VOXPIPE_S3_ENDPOINT_URL must be an HTTP(S) origin without embedded credentials")
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and parsed.hostname not in local_hosts:
            raise ValueError("VOXPIPE_S3_ENDPOINT_URL must use HTTPS outside loopback development endpoints")

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ValueError("S3-compatible storage requires the optional requirements-s3.txt dependencies") from None

    try:
        max_attempts = int(env.get("VOXPIPE_S3_MAX_ATTEMPTS", "3").strip())
    except (TypeError, ValueError):
        raise ValueError("VOXPIPE_S3_MAX_ATTEMPTS must be an integer") from None
    if not 1 <= max_attempts <= 5:
        raise ValueError("VOXPIPE_S3_MAX_ATTEMPTS must be between 1 and 5")

    connect_timeout = _positive_float(env, "VOXPIPE_S3_CONNECT_TIMEOUT_SECONDS", 5.0, 30.0)
    read_timeout = _positive_float(env, "VOXPIPE_S3_READ_TIMEOUT_SECONDS", 60.0, 120.0)
    config = Config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"max_attempts": max_attempts, "mode": "standard"},
        s3={"addressing_style": addressing_style},
    )
    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=config,
        )
    except Exception:
        raise ValueError("S3-compatible storage client could not be configured") from None

    return (
        S3CompatibleObjectStore(
            client,
            bucket=bucket,
            max_object_size_bytes=max_object_size_bytes,
            server_side_encryption=encryption,
            kms_key_id=kms_key_id,
        ),
        "s3_compatible",
    )
