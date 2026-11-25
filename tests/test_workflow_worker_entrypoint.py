"""The supervised worker exits its poll loop after SIGTERM or SIGINT."""
import logging
import signal
import unittest
from unittest.mock import patch

from scripts import run_workflow_worker


class _FakeRuntime:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    poll_interval_seconds = 1.0

    def __init__(self, handlers, requested_signal):
        self.handlers = handlers
        self.requested_signal = requested_signal
        self.stop_event = None

    def run_forever(self, stop_event):
        self.stop_event = stop_event
        self.handlers[self.requested_signal](self.requested_signal, None)


class WorkflowWorkerSafeStopTests(unittest.TestCase):
    def test_sigterm_and_sigint_request_clean_worker_stop(self):
        for requested_signal in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=requested_signal):
                handlers = {}
                runtime = _FakeRuntime(handlers, requested_signal)

                def register(signum, handler):
                    handlers[signum] = handler

                with patch.object(run_workflow_worker, "build_local_speech_worker", return_value=runtime), \
                     patch.object(run_workflow_worker.signal, "signal", side_effect=register), \
                     patch.object(run_workflow_worker.logging, "basicConfig"):
                    result = run_workflow_worker.main()

                self.assertEqual(result, 0)
                self.assertIn(signal.SIGTERM, handlers)
                self.assertIn(signal.SIGINT, handlers)
                self.assertTrue(runtime.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
