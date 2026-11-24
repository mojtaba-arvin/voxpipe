"""Static checks for the systemd worker template's security and lifecycle contract."""
import unittest
from pathlib import Path


class WorkflowSystemdUnitTests(unittest.TestCase):
    def test_template_is_tenant_scoped_supervised_and_hardened(self):
        path = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "voxpipe-workflow-worker@.service"
        content = path.read_text(encoding="utf-8")
        for required in (
            "User=voxpipe",
            "Group=voxpipe",
            "EnvironmentFile=/etc/voxpipe/workflow-worker.env",
            "Environment=VOXPIPE_WORKFLOW_TENANT_ID=%i",
            "ExecStart=/opt/voxpipe/.venv/bin/python /opt/voxpipe/scripts/run_workflow_worker.py",
            "Restart=on-failure",
            "RestartSec=5",
            "TimeoutStopSec=90",
            "KillSignal=SIGTERM",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ReadWritePaths=/var/lib/voxpipe",
        ):
            with self.subTest(required=required):
                self.assertIn(required, content)

    def test_unit_does_not_embed_credentials_or_enable_hosted_stt(self):
        path = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "voxpipe-workflow-worker@.service"
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("DEEPGRAM_API_KEY", content)
        self.assertNotIn("api.deepgram.com", content)
        self.assertNotIn("postgresql://", content)


if __name__ == "__main__":
    unittest.main()
