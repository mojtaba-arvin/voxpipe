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
        self.assertNotIn("alignment_cv.json", workflow)
        self.assertNotIn("speaker_segments.json", workflow)
        self.assertNotIn("--intermediate-dir", workflow)
        self.assertNotIn("WhisperX", workflow)
        self.assertNotIn("predictions.jsonl", artifact_step)
        self.assertNotIn("words.json", artifact_step)
        self.assertIn("--max-clips 50", workflow)
        self.assertIn("--candidate-scan-limit 100", workflow)


if __name__ == "__main__":
    unittest.main()
