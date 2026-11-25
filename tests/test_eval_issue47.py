from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from eval.issue47.cpu_smoke import build_report, MODEL_REVISION, MODEL_ID


class CpuSmokeReportTests(unittest.TestCase):
    def test_report_is_reproducible_and_discloses_english_only_scope(self) -> None:
        report = build_report(
            model_download_seconds=1.25,
            model_size_bytes=1880000000,
            model_load_seconds=12.5,
            inference_seconds=4.0,
            audio_seconds=8.0,
            cpu_count=8,
            torch_num_threads=4,
            detected_language="English",
            transcript="short sample transcription",
            torch_version="2.8.0+cpu",
            qwen_asr_version="0.0.6",
        )
        self.assertEqual(report["model_id"], MODEL_ID)
        self.assertEqual(report["model_revision"], MODEL_REVISION)
        self.assertEqual(report["real_time_factor"], 0.5)
        self.assertEqual(report["device"], "cpu")
        self.assertFalse(report["timestamps_returned"])
        self.assertTrue(any("does not measure Persian WER" in item for item in report["warnings"]))
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "eval" / "issue47" / "cpu_smoke.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report)

    def test_report_rejects_empty_transcript_or_invalid_timings(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            build_report(
                model_download_seconds=0,
                model_size_bytes=1,
                model_load_seconds=1,
                inference_seconds=0,
                audio_seconds=2,
                cpu_count=2,
                torch_num_threads=2,
                detected_language="English",
                transcript="x",
                torch_version="2.8.0",
                qwen_asr_version="0.0.6",
            )
        with self.assertRaisesRegex(ValueError, "must be non-empty"):
            build_report(
                model_download_seconds=0,
                model_size_bytes=1,
                model_load_seconds=1,
                inference_seconds=1,
                audio_seconds=2,
                cpu_count=2,
                torch_num_threads=2,
                detected_language="English",
                transcript="",
                torch_version="2.8.0",
                qwen_asr_version="0.0.6",
            )


if __name__ == "__main__":
    unittest.main()
