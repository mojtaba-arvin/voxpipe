from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import wave
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from eval.issue47.qwen_transcription import (
    _parse_persian_transcription,
    corpus_error_rates,
    run_evaluation,
)


class QwenPersianEvaluationTests(unittest.TestCase):
    def test_auto_detected_language_must_be_persian(self) -> None:
        self.assertEqual(
            _parse_persian_transcription({"language": "Persian", "transcription": "سلام دنیا"}),
            "سلام دنیا",
        )
        with self.assertRaisesRegex(RuntimeError, "unexpected language"):
            _parse_persian_transcription({"language": "English", "transcription": "hello world"})
        with self.assertRaisesRegex(RuntimeError, "empty Persian transcription"):
            _parse_persian_transcription({"language": "Persian", "transcription": " "})

    def test_persian_normalization_and_error_rates(self) -> None:
        metrics = corpus_error_rates(["يک كتاب"], ["یک کتاب"])
        self.assertEqual(metrics["wer"], 0.0)
        self.assertEqual(metrics["cer"], 0.0)

    def test_word_and_character_errors_are_aggregated(self) -> None:
        metrics = corpus_error_rates(["سلام دنیا"], ["سلام جهان"])
        self.assertEqual(metrics["word_errors"], 1)
        self.assertEqual(metrics["reference_words"], 2)
        self.assertEqual(metrics["wer"], 0.5)
        self.assertGreater(metrics["cer"], 0)

    def test_empty_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-whitespace"):
            corpus_error_rates(["  "], ["متن"])

    def test_pipeline_writes_aggregate_only_report_and_uses_ephemeral_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "sample.tar.gz"
            audio_path = root / "clip.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 16000)
            tsv_path = root / "validated.tsv"
            tsv_path.write_text(
                "client_id\tpath\tsentence\nprivate-client\tclip.wav\tسلام دنیا\n",
                encoding="utf-8",
            )
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(audio_path, arcname="clips/clip.wav")
                archive.add(tsv_path, arcname="validated.tsv")
            report_path = root / "result.json"
            with patch("eval.issue47.qwen_transcription._transcribe_audio", return_value="سلام دنیا"):
                report = run_evaluation(
                    archive_path, report_path, root / "cache",
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                )
            self.assertEqual(report["sample_count"], 1)
            self.assertEqual(report["wer"], 0.0)
            self.assertFalse(report["transcript_text_uploaded"])
            self.assertFalse(report["audio_uploaded"])
            serialized = report_path.read_text(encoding="utf-8").lower()
            self.assertNotIn("سلام دنیا", serialized)
            self.assertNotIn("private-client", serialized)
            intermediate_dir = root / "alignment-input"
            with patch("eval.issue47.qwen_transcription._transcribe_audio", return_value="سلام دنیا"):
                intermediate_report = run_evaluation(
                    archive_path, root / "intermediate-result.json", root / "cache",
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    intermediate_dir=intermediate_dir,
                )
            manifest_rows = [json.loads(line) for line in (intermediate_dir / "alignment_input.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(manifest_rows), 1)
            self.assertEqual(manifest_rows[0]["transcript"], "سلام دنیا")
            self.assertTrue(Path(manifest_rows[0]["audio_path"]).is_file())
            self.assertFalse(intermediate_report["transcript_text_uploaded"])
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "eval" / "issue47" / "qwen3_asr_persian.schema.json").read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(report)


if __name__ == "__main__":
    unittest.main()
