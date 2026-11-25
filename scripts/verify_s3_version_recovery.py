#!/usr/bin/env python3
"""Verify versioned durable objects can be restored after a delete marker."""

from __future__ import annotations

import os
import uuid

import boto3
from botocore.config import Config


def main() -> int:
    env = os.environ
    client = boto3.client(
        "s3",
        endpoint_url=env["VOXPIPE_S3_ENDPOINT_URL"],
        region_name=env["VOXPIPE_S3_REGION"],
        aws_access_key_id=env["SILO_ROOT_ACCESS_KEY_ID"],
        aws_secret_access_key=env["SILO_ROOT_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": env.get("VOXPIPE_S3_ADDRESSING_STYLE", "path")}),
    )
    bucket = env["VOXPIPE_S3_RECOVERY_BUCKET"]
    key = f"tenant/_system/version-recovery/{uuid.uuid4()}"
    original = b"voxpipe-version-recovery-v1"
    client.put_object(Bucket=bucket, Key=key, Body=original, ServerSideEncryption="AES256")
    versions = client.list_object_versions(Bucket=bucket, Prefix=key)
    version = next(
        (item for item in versions.get("Versions", []) if item.get("Key") == key),
        None,
    )
    if version is None:
        raise RuntimeError("versioning did not retain the original durable object")
    client.delete_object(Bucket=bucket, Key=key)
    client.copy_object(
        Bucket=bucket,
        Key=key,
        CopySource={"Bucket": bucket, "Key": key, "VersionId": version["VersionId"]},
        ServerSideEncryption="AES256",
    )
    restored = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if restored != original:
        raise RuntimeError("restored durable object content did not match the original")

    remaining = client.list_object_versions(Bucket=bucket, Prefix=key)
    objects = [
        {"Key": item["Key"], "VersionId": item["VersionId"]}
        for collection in ("Versions", "DeleteMarkers")
        for item in remaining.get(collection, [])
        if item.get("Key") == key
    ]
    if objects:
        client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
    print("Version recovery verified; disposable recovery object and versions removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
