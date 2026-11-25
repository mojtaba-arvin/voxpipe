#!/usr/bin/env python3
"""Verify terminal reconciliation removes staging but preserves the durable asset."""

from __future__ import annotations

import argparse
import os
from uuid import UUID

from voxpipe.storage.factory import build_object_store
from voxpipe.storage.ports import ObjectNotFoundError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()

    tenant_id = str(UUID(args.tenant_id))
    session_id = str(UUID(args.session_id))
    asset_id = str(UUID(args.asset_id))
    store, provider = build_object_store(
        os.environ,
        local_root=None,
        local_root_env="VOXPIPE_OBJECT_STORE_ROOT",
        max_object_size_bytes=512 * 1024 * 1024,
    )
    if provider != "s3_compatible":
        raise RuntimeError("deployment E2E did not select S3-compatible storage")

    staging_key = f"staging/v1/{tenant_id}/{session_id}/source"
    try:
        store.head(staging_key)
    except ObjectNotFoundError:
        pass
    else:
        raise RuntimeError("terminal reconciliation left the staging object behind")

    durable_key = f"tenant/{tenant_id}/assets/{asset_id}/source"
    durable = store.head(durable_key)
    if durable.sha256 != args.expected_sha256:
        raise RuntimeError("durable object checksum differs from the uploaded audio")
    print("Separate-container E2E passed: staging removed, durable object retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
