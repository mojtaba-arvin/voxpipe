#!/usr/bin/env python3
"""Apply the disposable bucket's versioning and staging lifecycle policy."""

from __future__ import annotations

import os

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
    bucket = env["VOXPIPE_S3_BUCKET"]
    days = int(env["VOXPIPE_S3_STAGING_LIFECYCLE_DAYS"])
    if days < 1:
        raise ValueError("staging lifecycle days must be positive")
    client.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "voxpipe-staging-expiration",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "staging/v1/"},
                    "Expiration": {"Days": days},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": days},
                }
            ]
        },
    )
    print("Disposable S3 bucket versioning and staging lifecycle configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
