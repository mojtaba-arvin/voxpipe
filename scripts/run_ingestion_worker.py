"""Run the durable ingestion worker for explicitly authorized tenants."""

from __future__ import annotations

import logging
import os
import signal
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

from voxpipe.ingestion import (
    IngestionJobWorker,
    PostgresIngestionJobQueue,
    PostgresIngestionRepository,
)
from voxpipe.ingestion.worker import run_forever
from voxpipe.ingestion.service import IngestionService
from voxpipe.storage.factory import build_object_store


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
    if not dsn or not staging_root or not tenants:
        raise SystemExit(
            "VOXPIPE_DATABASE_DSN, VOXPIPE_STAGING_ROOT, and an explicit "
            "VOXPIPE_TENANT_IDS allowlist are required; local storage also requires VOXPIPE_OBJECT_ROOT"
        )
    if len(set(tenants)) != len(tenants):
        raise SystemExit("VOXPIPE_TENANT_IDS must not contain duplicates")

    worker_id_value = os.environ.get("VOXPIPE_WORKER_ID", "").strip()
    try:
        worker_id = str(UUID(worker_id_value)) if worker_id_value else str(uuid4())
    except ValueError:
        raise SystemExit("VOXPIPE_WORKER_ID must be a UUID")
    try:
        lease_seconds = int(os.environ.get("VOXPIPE_JOB_LEASE_SECONDS", "90"))
        poll_seconds = float(os.environ.get("VOXPIPE_JOB_POLL_SECONDS", "1"))
        max_upload = int(os.environ.get("VOXPIPE_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
        max_backoff = float(os.environ.get("VOXPIPE_WORKER_MAX_BACKOFF_SECONDS", "30"))
    except ValueError:
        raise SystemExit("worker limits must be numeric")
    if lease_seconds < 3 or poll_seconds <= 0 or max_upload < 1 or max_backoff <= 0:
        raise SystemExit("worker lease, polling, upload, and backoff limits are invalid")

    repository = PostgresIngestionRepository(dsn)
    queue = PostgresIngestionJobQueue(dsn)
    object_store, storage_provider = build_object_store(
        os.environ,
        local_root=object_root or None,
        local_root_env="VOXPIPE_OBJECT_ROOT",
        max_object_size_bytes=max_upload,
    )
    service = IngestionService(
        staging_root=staging_root,
        repository=repository,
        object_store=object_store,
        max_file_size_bytes=max_upload,
        storage_provider=storage_provider,
        lease_ttl=timedelta(seconds=max(1, lease_seconds // 2)),
    )
    worker = IngestionJobWorker(
        queue=queue,
        service=service,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )

    stop_event = Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info("worker started id=%s authorized_tenants=%d", worker_id, len(tenants))
    run_forever(
        worker,
        tenants,
        stop_event,
        poll_seconds=poll_seconds,
        max_backoff_seconds=max_backoff,
        logger=logger,
    )
    logger.info("worker stopped id=%s", worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
