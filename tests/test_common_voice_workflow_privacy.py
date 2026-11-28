from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CommonVoiceWorkflowPrivacyTests(unittest.TestCase):
    def test_common_voice_artifact_contains_only_aggregate_reports(self):
        workflow = (ROOT / ".github" / "workflows" / "common-voice-asr.yml").read_text(
            encoding="utf-8"
        )
        artifact_step = workflow.split(
            "      - name: Upload aggregate WER/CER and provenance only", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn("eval/artifacts/issue47/common_voice_archive.json", artifact_step)
        self.assertIn("eval/artifacts/issue47/qwen3_asr_persian.json", artifact_step)
        self.assertIn("eval/artifacts/issue47/alignment_cv.json", artifact_step)
        self.assertIn("eval/artifacts/issue47/speaker_segments.json", artifact_step)
        self.assertIn("--intermediate-dir \"$RUNNER_TEMP/voxpipe-qwen-alignment-input\"", workflow)
        self.assertIn("--candidate-scan-limit 100", workflow)
        self.assertIn("Run Persian WhisperX forced alignment on the exact Qwen hypotheses", workflow)
        self.assertIn('rm -rf "$RUNNER_TEMP/voxpipe-qwen-alignment-input"', workflow)
        self.assertNotIn("alignment_input.jsonl", artifact_step)
        self.assertNotIn("predictions.jsonl", artifact_step)
        self.assertNotIn("words.json", artifact_step)


if __name__ == "__main__":
    unittest.main()
