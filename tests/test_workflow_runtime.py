"""Worker runtime composition tests; no PostgreSQL or model is started."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from voxpipe.workflows.runtime import build_local_speech_worker


class WorkflowWorkerRuntimeTests(unittest.TestCase):
    def env(self):
        return {
            "VOXPIPE_WORKFLOW_DATABASE_DSN": "postgresql://unit-test",
            "VOXPIPE_WORKFLOW_TENANT_ID": "11111111-1111-4111-8111-111111111111",
            "VOXPIPE_OBJECT_STORE_ROOT": "/tmp/voxpipe-test-objects",
            "VOXPIPE_WORKFLOW_WORKER_ID": "22222222-2222-4222-8222-222222222222",
        }

    @patch("voxpipe.workflows.runtime.build_speech_resilience_runtime")
    @patch("voxpipe.workflows.runtime.PostgresWorkflowWorker")
    @patch("voxpipe.workflows.runtime.WorkflowHandlerRegistry")
    @patch("voxpipe.workflows.runtime.SpeechTranscriptionWorkflowHandler")
    @patch("voxpipe.workflows.runtime.FasterWhisperAdapter")
    @patch("voxpipe.workflows.runtime.build_object_store")
    @patch("voxpipe.workflows.runtime.PostgresIngestionRepository")
    @patch("voxpipe.workflows.runtime.PostgresWorkflowRepository")
    def test_composes_only_local_allowlisted_speech_handler(
        self, workflow_repo, asset_repo, object_store, adapter, handler, registry, worker, resilience_factory,
    ):
        env = self.env()
        env.update({
            "VOXPIPE_CIRCUIT_BREAKER_BACKEND": "postgres",
            "VOXPIPE_DATABASE_DSN": "postgresql://breaker-role@db/voxpipe",
        })
        guarded_port = object()
        resilience_factory.return_value = SimpleNamespace(speech_port=guarded_port, backend="postgres")
        object_store.return_value = (object(), "local")
        runtime = build_local_speech_worker(env)
        object_store.assert_called_once_with(
            env,
            local_root="/tmp/voxpipe-test-objects",
            local_root_env="VOXPIPE_OBJECT_STORE_ROOT",
            max_object_size_bytes=512 * 1024 * 1024,
        )
        workflow_repo.assert_called_once_with("postgresql://unit-test")
        asset_repo.assert_called_once_with("postgresql://unit-test")
        adapter.assert_called_once()
        self.assertTrue(adapter.call_args.kwargs["local_files_only"])
        self.assertEqual(adapter.call_args.kwargs["max_audio_bytes"], 512 * 1024 * 1024)
        resilience_factory.assert_called_once_with(adapter.return_value, environ=env)
        handler.assert_called_once()
        self.assertIs(handler.call_args.kwargs["adapter"], guarded_port)
        registry.assert_called_once()
        self.assertEqual(set(registry.call_args.args[0]), {"speech.transcribe"})
        worker.assert_called_once()
        self.assertEqual(runtime.tenant_id, "11111111-1111-4111-8111-111111111111")
        self.assertEqual(runtime.poll_interval_seconds, 1.0)

    def test_requires_database_tenant_and_object_store_configuration(self):
        with self.assertRaisesRegex(ValueError, "VOXPIPE_WORKFLOW_DATABASE_DSN"):
            build_local_speech_worker({})
        env = self.env()
        del env["VOXPIPE_WORKFLOW_TENANT_ID"]
        with self.assertRaisesRegex(ValueError, "VOXPIPE_WORKFLOW_TENANT_ID"):
            build_local_speech_worker(env)
        env = self.env()
        del env["VOXPIPE_OBJECT_STORE_ROOT"]
        with self.assertRaisesRegex(ValueError, "VOXPIPE_OBJECT_STORE_ROOT"):
            build_local_speech_worker(env)

    def test_rejects_invalid_tenant_worker_and_bounds(self):
        env = self.env()
        env["VOXPIPE_WORKFLOW_TENANT_ID"] = "not-a-uuid"
        with self.assertRaisesRegex(ValueError, "must be a UUID"):
            build_local_speech_worker(env)
        env = self.env()
        env["VOXPIPE_WORKFLOW_SCAN_LIMIT"] = "1001"
        with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
            build_local_speech_worker(env)
        env = self.env()
        env["VOXPIPE_WORKFLOW_POLL_INTERVAL_SECONDS"] = "nan"
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            build_local_speech_worker(env)

    def test_defaults_to_cpu_int8_local_model_without_network_downloads(self):
        env = self.env()
        env.pop("VOXPIPE_WORKFLOW_WORKER_ID")
        with patch("voxpipe.workflows.runtime.PostgresWorkflowRepository"), \
             patch("voxpipe.workflows.runtime.PostgresIngestionRepository"), \
             patch("voxpipe.workflows.runtime.build_object_store", return_value=(object(), "local")), \
             patch("voxpipe.workflows.runtime.FasterWhisperAdapter") as adapter, \
             patch("voxpipe.workflows.runtime.SpeechTranscriptionWorkflowHandler"), \
             patch("voxpipe.workflows.runtime.WorkflowHandlerRegistry"), \
             patch("voxpipe.workflows.runtime.PostgresWorkflowWorker"):
            build_local_speech_worker(env)
        self.assertEqual(adapter.call_args.kwargs["device"], "cpu")
        self.assertEqual(adapter.call_args.kwargs["compute_type"], "int8")
        self.assertTrue(adapter.call_args.kwargs["local_files_only"])
        self.assertEqual(len(adapter.call_args.kwargs), 6)


if __name__ == "__main__":
    unittest.main()
