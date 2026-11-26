from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from eval.issue47.whisperx_alignment import run_alignment


class WhisperXPersianAlignmentTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path]:
        audio = root / "sample.wav"
        audio.write_bytes(b"fixture")
        intermediates = root / "qwen.jsonl"
        intermediates.write_text(json.dumps({
            "sample_id": "sample-001",
            "audio_path": str(audio),
            "reference": "سلام دنیا",
            "hypothesis": "سلام دنیا",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        return intermediates, audio

    def test_aggregate_alignment_and_empty_diarization_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _ = self._inputs(root)
            model = object()
            metadata = {"language": "fa", "type": "huggingface", "dictionary": {}}

            def fake_aligner(transcript, loaded_model, loaded_metadata, audio):
                self.assertIs(loaded_model, model)
                self.assertEqual(transcript[0]["text"], "سلام دنیا")
                return {"segments": [{"words": [
                    {"word": "سلام", "start": 0.1, "end": 0.4, "score": 0.9},
                    {"word": "دنیا", "start": 0.5, "end": 0.9, "score": 0.8},
                ]}]}

            report = run_alignment(
                inputs, root / "report.json", root / "speaker_segments.json",
                root / "alignment_cv.jsonl", root / "cache",
                model_loader=lambda _: (model, metadata),
                aligner=fake_aligner,
                duration_fn=lambda _: 1.0,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["alignment_coverage"], 1.0)
            self.assertEqual(report["batch_size"], 1)
            self.assertEqual(report["compute_type"], "float32")
            self.assertEqual(report["diarization_status"], "not_evaluated_read_speech")
            speaker = json.loads((root / "speaker_segments.json").read_text(encoding="utf-8"))
            self.assertEqual(speaker["speaker_segments"], [])
            self.assertFalse(speaker["speaker_ids_used"])
            self.assertFalse(report["word_timestamps_uploaded"])
            self.assertNotIn("سلام دنیا", (root / "report.json").read_text(encoding="utf-8"))
            alignment_lines = (root / "alignment_cv.jsonl").read_text(encoding="utf-8")
            self.assertIn('"start":0.1', alignment_lines)
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "eval" / "issue47" / "whisperx_alignment.schema.json").read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(report)

    def test_alignment_failure_is_reported_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _ = self._inputs(root)
            report = run_alignment(
                inputs, root / "report.json", root / "speaker_segments.json",
                root / "alignment_cv.jsonl", root / "cache",
                model_loader=lambda _: (object(), {}),
                aligner=lambda *args: (_ for _ in ()).throw(RuntimeError("fixture failure")),
                duration_fn=lambda _: 1.0,
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["failed_sample_count"], 1)
            self.assertEqual(report["failure_reasons"], {"RuntimeError": 1})

    def test_duplicate_sample_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, audio = self._inputs(root)
            record = {
                "sample_id": "sample-001",
                "audio_path": str(audio),
                "reference": "سلام",
                "hypothesis": "سلام",
            }
            with inputs.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            from eval.issue47.whisperx_alignment import load_intermediates
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_intermediates(inputs)


if __name__ == "__main__":
    unittest.main()
