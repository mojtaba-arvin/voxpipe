#!/usr/bin/env python3
"""Print tenant-scoped durable ingestion queue health metrics."""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

from voxpipe.ingestion.jobs import PostgresIngestionJobQueue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="UUID tenant scope required by PostgreSQL RLS")
    args = parser.parse_args()
    tenant_id = str(UUID(args.tenant_id))
    dsn = os.environ.get("VOXPIPE_DATABASE_DSN", "").strip()
    if not dsn:
        parser.error("VOXPIPE_DATABASE_DSN is required")
    queue = PostgresIngestionJobQueue(dsn)
    print(json.dumps({
        "tenant_id": tenant_id,
        "queue": queue.metrics(tenant_id),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
