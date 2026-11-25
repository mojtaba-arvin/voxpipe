#!/usr/bin/env python3
"""Fail closed when staging lifecycle expiry is shorter than recovery windows."""

from __future__ import annotations

import math
import os

import boto3
from botocore.config import Config


def minimum_staging_retention_days(
    session_ttl_seconds: int,
    max_attempts: int,
    lease_seconds: int,
    max_backoff_seconds: float,
    recovery_window_seconds: int,
    safety_margin_days: int = 7,
) -> int:
    if min(session_ttl_seconds, max_attempts, lease_seconds, recovery_window_seconds, safety_margin_days) < 0:
        raise ValueError("retention inputs cannot be negative")
    if max_attempts < 1 or lease_seconds < 1 or max_backoff_seconds < 0:
        raise ValueError("retry and lease inputs are invalid")
    retry_window = max_attempts * (lease_seconds + max_backoff_seconds)
    total = session_ttl_seconds + retry_window + recovery_window_seconds + safety_margin_days * 86400
    return math.ceil(total / 86400)


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
    versioning = client.get_bucket_versioning(Bucket=bucket)
    if versioning.get("Status") != "Enabled":
        raise RuntimeError("bucket versioning must be enabled")

    lifecycle = client.get_bucket_lifecycle_configuration(Bucket=bucket)
    minimum_days = minimum_staging_retention_days(
        session_ttl_seconds=int(env.get("VOXPIPE_SESSION_TTL_SECONDS", "86400")),
        max_attempts=int(env.get("VOXPIPE_JOB_MAX_ATTEMPTS", "5")),
        lease_seconds=int(env.get("VOXPIPE_JOB_LEASE_SECONDS", "90")),
        max_backoff_seconds=float(env.get("VOXPIPE_WORKER_MAX_BACKOFF_SECONDS", "30")),
        recovery_window_seconds=int(env.get("VOXPIPE_S3_STAGING_RECOVERY_WINDOW_SECONDS", "2592000")),
        safety_margin_days=int(env.get("VOXPIPE_S3_STAGING_SAFETY_MARGIN_DAYS", "7")),
    )
    configured_days = int(env["VOXPIPE_S3_STAGING_LIFECYCLE_DAYS"])
    if configured_days < minimum_days:
        raise RuntimeError(
            f"staging lifecycle is {configured_days} days; minimum safe retention is {minimum_days} days"
        )

    rules = lifecycle.get("Rules", [])
    staging_rules = [
        rule for rule in rules
        if rule.get("Status") == "Enabled"
        and rule.get("Filter", {}).get("Prefix") == "staging/v1/"
    ]
    if len(staging_rules) != 1:
        raise RuntimeError("exactly one enabled staging/v1 lifecycle rule is required")
    rule = staging_rules[0]
    expiration_days = rule.get("Expiration", {}).get("Days")
    noncurrent_days = rule.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays")
    if expiration_days is None or expiration_days < minimum_days:
        raise RuntimeError("staging current-version expiry is shorter than the safe retention window")
    if noncurrent_days is None or noncurrent_days < minimum_days:
        raise RuntimeError("staging noncurrent-version expiry is shorter than the safe retention window")

    for candidate in rules:
        prefix = candidate.get("Filter", {}).get("Prefix", "")
        if candidate is rule:
            continue
        if candidate.get("Status") == "Enabled" and candidate.get("Expiration") and (
            prefix == "" or prefix.startswith("tenant/")
        ):
            raise RuntimeError("lifecycle policy must not expire all objects or durable tenant objects")

    print(f"S3 staging lifecycle verified: {configured_days} days (minimum {minimum_days}); versioning enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
