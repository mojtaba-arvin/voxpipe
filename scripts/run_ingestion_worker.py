"""Run one durable ingestion worker against explicitly authorized tenants."""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import timedelta
from uuid import UUID, uuid4

from voxpipe.ingestion import (
    IngestionJobWorker,
    PostgresIngestionJobQueue,
    PostgresIngestionRepository,
)
from voxpipe.ingestion.service import IngestionService
from voxpipe.storage.local import LocalFilesystemObjectStore


def main() -> int:
    logging.basicConfig(
        level=os.getenv("VOXPIPE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("voxpipe.worker")

    dsn = os.environ.get("VOXPIPE_DATABASE_DSN", "").strip()
    staging_root = os.environ.get("VOXPIPE_STAGING_ROOT", "").strip()
    object_root = os.environ.get("VOXPIPE_OBJECT_ROOT", "").strip()
    tenant_values = os.environ.get("VOXPIPE_TENANT_IDS", "").split(",")
    tenants: list[str] = []
    for value in tenant_values:
        value = value.strip()
        if not value:
            continue
        try:
            tenants.append(str(UUID(value)))
        except ValueError:
            raise SystemExit("VOXPIPE_TENANT_IDS must contain only UUID values")
    if not dsn or not staging_root or not object_root or not tenants:
        raise SystemExit(
            "VOXPIPE_DATABASE_DSN, VOXPIPE_STAGING_ROOT, VOXPIPE_OBJECT_ROOT, "
            "and an explicit VOXPIPE_TENANT_IDS allowlist are required"
        )

    worker_id_value = os.environ.get("VOXPIPE_WORKER_ID", "").strip()
    worker_id = str(UUID(worker_id_value)) if worker_id_value else str(uuid4())
    lease_seconds = int(os.environ.get("VOXPIPE_JOB_LEASE_SECONDS", "90"))
    poll_seconds = float(os.environ.get("VOXPIPE_JOB_POLL_SECONDS", "1"))
    max_upload = int(os.environ.get("VOXPIPE_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
    if poll_seconds <= 0 or max_upload < 1:
        raise SystemExit("worker poll interval and upload limit must be positive")

    repository = PostgresIngestionRepository(dsn)
    queue = PostgresIngestionJobQueue(dsn)
    object_store = LocalFilesystemObjectStore(
        object_root,
        max_object_size_bytes=max_upload,
    )
    service = IngestionService(
        staging_root=staging_root,
        repository=repository,
        object_store=object_store,
        max_file_size_bytes=max_upload,
        storage_provider="local",
        # The durable job lease is authoritative for queued sessions. A shorter
        # session lease ensures a restarted worker is not blocked by stale state.
        lease_ttl=timedelta(seconds=max(1, lease_seconds // 2)),
    )
    worker = IngestionJobWorker(
        queue=queue,
        service=service,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )

    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info("worker started id=%s authorized_tenants=%d", worker_id, len(tenants))

    while running:
        did_work = False
        for tenant_id in tenants:
            if not running:
                break
            result = worker.run_once(tenant_id)
            if result.outcome != "idle":
                did_work = True
                logger.info(
                    "job execution job_id=%s outcome=%s error_code=%s",
                    result.job_id,
                    result.outcome,
                    result.error_code or "-",
                )
        if not did_work and running:
            time.sleep(poll_seconds)

    logger.info("worker stopped id=%s", worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
