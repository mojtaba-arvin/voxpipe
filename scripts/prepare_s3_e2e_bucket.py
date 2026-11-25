#!/usr/bin/env python3
"""Create the disposable S3 bucket used by the separate-container E2E."""

from __future__ import annotations

import os
import time

import boto3
from botocore.config import Config


def main() -> int:
    env = os.environ
    bucket = env["VOXPIPE_S3_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=env["VOXPIPE_S3_ENDPOINT_URL"],
        region_name=env["VOXPIPE_S3_REGION"],
        aws_access_key_id=env["VOXPIPE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=env["VOXPIPE_S3_SECRET_ACCESS_KEY"],
        config=Config(
            retries={"max_attempts": int(env.get("VOXPIPE_S3_MAX_ATTEMPTS", "3"))},
            connect_timeout=float(env.get("VOXPIPE_S3_CONNECT_TIMEOUT_SECONDS", "5")),
            read_timeout=float(env.get("VOXPIPE_S3_READ_TIMEOUT_SECONDS", "60")),
            s3={"addressing_style": env.get("VOXPIPE_S3_ADDRESSING_STYLE", "path")},
        ),
    )
    last_error: Exception | None = None
    for attempt in range(30):
        try:
            try:
                client.head_bucket(Bucket=bucket)
            except Exception:
                client.create_bucket(Bucket=bucket)
            print("Disposable S3 bucket is ready.")
            return 0
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("S3-compatible service did not become ready") from last_error


if __name__ == "__main__":
    raise SystemExit(main())
