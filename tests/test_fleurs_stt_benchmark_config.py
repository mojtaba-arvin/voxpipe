"""Allowlisted local Faster-Whisper benchmark model selection."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_fleurs_stt_benchmark.py"
SPEC = importlib.util.spec_from_file_location("voxpipe_fleurs_benchmark", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FleursBenchmarkModelConfigTests(unittest.TestCase):
    def test_resolves_only_reviewed_public_model_repositories(self):
        self.assertEqual(MODULE.resolve_model_id("small"), "Systran/faster-whisper-small")
        self.assertEqual(MODULE.resolve_model_id("medium"), "Systran/faster-whisper-medium")
        self.assertEqual(MODULE.resolve_model_id("large-v3"), "Systran/faster-whisper-large-v3")

    def test_rejects_unreviewed_model_repository_input(self):
        with self.assertRaisesRegex(ValueError, "small, medium, large-v3"):
            MODULE.resolve_model_id("attacker/arbitrary-model")


if __name__ == "__main__":
    unittest.main()
