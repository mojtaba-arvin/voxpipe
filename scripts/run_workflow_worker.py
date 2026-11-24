"""Run one tenant's local speech workflow worker under an OS supervisor."""
from __future__ import annotations

import logging
import signal
from threading import Event

from voxpipe.workflows.runtime import build_local_speech_worker


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = build_local_speech_worker()
    stop_event = Event()

    def request_stop(signum, _frame):
        logging.getLogger("voxpipe.workflow.worker").info(
            "shutdown requested signal=%s", signum,
        )
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    logging.getLogger("voxpipe.workflow.worker").info(
        "worker started tenant=%s poll_interval_seconds=%s",
        runtime.tenant_id, runtime.poll_interval_seconds,
    )
    runtime.run_forever(stop_event)
    logging.getLogger("voxpipe.workflow.worker").info("worker stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
