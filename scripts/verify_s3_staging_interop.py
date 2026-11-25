#!/usr/bin/env python3
"""Verify and remove a staging object written by the Go S3 stager."""

from __future__ import annotations

import hashlib
import os

import boto3
from botocore.config import Config

from voxpipe.storage.factory import build_object_store


TENANT_ID = "7f9b7b3c-5b5a-4b90-a1b0-9b4a7d8e2f10"
SESSION_ID = "e41a2e87-b4e2-4b1c-8e3a-8c2e2b8c3e21"
EXPECTED_BODY = b"cross-language staging integration fixture"


def main() -> int:
    env = os.environ
    store, provider = build_object_store(
        env,
        local_root=None,
        local_root_env="VOXPIPE_OBJECT_STORE_ROOT",
        max_object_size_bytes=512 * 1024 * 1024,
    )
    if provider != "s3_compatible":
        raise SystemExit("cross-language staging check requires S3-compatible storage")

    key = f"staging/v1/{TENANT_ID}/{SESSION_ID}/source"
    expected_digest = hashlib.sha256(EXPECTED_BODY).hexdigest()
    info = store.head(key)
    if info.size_bytes != len(EXPECTED_BODY) or info.sha256 != expected_digest:
        raise SystemExit("Python worker observed different Go-staged object metadata")
    with store.open(key) as source:
        body = source.read()
    if body != EXPECTED_BODY or hashlib.sha256(body).hexdigest() != expected_digest:
        raise SystemExit("Python worker could not read the exact Go-staged object")

    if not store.delete(key):
        raise SystemExit("Python worker failed to remove the Go-staged object")

    endpoint = env.get("VOXPIPE_S3_ENDPOINT_URL", "").strip() or None
    style = env.get("VOXPIPE_S3_ADDRESSING_STYLE", "path").strip().lower()
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=env["VOXPIPE_S3_REGION"],
        aws_access_key_id=env.get("VOXPIPE_S3_ACCESS_KEY_ID") or None,
        aws_secret_access_key=env.get("VOXPIPE_S3_SECRET_ACCESS_KEY") or None,
        config=Config(
            retries={"max_attempts": int(env.get("VOXPIPE_S3_MAX_ATTEMPTS", "3"))},
            connect_timeout=float(env.get("VOXPIPE_S3_CONNECT_TIMEOUT_SECONDS", "5")),
            read_timeout=float(env.get("VOXPIPE_S3_READ_TIMEOUT_SECONDS", "60")),
            s3={"addressing_style": style},
        ),
    )
    client.delete_bucket(Bucket=env["VOXPIPE_S3_BUCKET"])
    print("Go S3Stager -> Python ObjectStoragePort interoperability passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
