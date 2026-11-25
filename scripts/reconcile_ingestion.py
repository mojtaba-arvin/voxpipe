#!/usr/bin/env python3
"""Run tenant-scoped staging and retention reconciliation."""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

from voxpipe.ingestion.recovery import IngestionOrphanReconciler
from voxpipe.ingestion.postgres import PostgresIngestionRepository
from voxpipe.storage.factory import build_object_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="UUID tenant scope required by PostgreSQL RLS")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    tenant_id = str(UUID(args.tenant_id))
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")

    dsn = os.environ.get("VOXPIPE_DATABASE_DSN", "").strip()
    staging_root = os.environ.get("VOXPIPE_STAGING_ROOT", "").strip()
    object_root = os.environ.get("VOXPIPE_OBJECT_STORE_ROOT", "").strip()
    if not dsn or not staging_root:
        parser.error("VOXPIPE_DATABASE_DSN and VOXPIPE_STAGING_ROOT are required; local storage also requires VOXPIPE_OBJECT_STORE_ROOT")

    repository = PostgresIngestionRepository(dsn)
    object_store, _storage_provider = build_object_store(\n        os.environ,\n        local_root=object_root or None,\n        local_root_env="VOXPIPE_OBJECT_STORE_ROOT",\n        max_object_size_bytes=512 * 1024 * 1024,\n    )
    reconciler = IngestionOrphanReconciler(
        repository=repository,
        object_store=object_store,
        staging_root=staging_root,
    )
    report = reconciler.run(tenant_id=tenant_id, limit=args.limit)
    print(json.dumps({
        "tenant_id": tenant_id,
        "cleaned": report.cleaned,
        "errors": report.errors,
        "retention_deleted": report.retention_deleted,
        "items": [
            {
                "session_id": item.session_id,
                "staging_deleted": item.staging_deleted,
                "asset_deleted": item.asset_deleted,
                "object_deleted": item.object_deleted,
                "error_code": item.error_code,
            }
            for item in report.items
        ],
    }, sort_keys=True))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
