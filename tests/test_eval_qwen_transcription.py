from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from eval.issue47.qwen_transcription import (
    corpus_error_rates,
    run_evaluation,
)


class QwenPersianEvaluationTests(unittest.TestCase):
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
                audio.writeframes(b"\\x00\\x00" * 16000)
            tsv_path = root / "validated.tsv"
            tsv_path.write_text(
                "client_id\tpath\tsentence\nprivate-client\tclip.wav\tسلام دنیا\n",
                encoding="utf-8",
            )
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(audio_path, arcname="clips/clip.wav")
                archive.add(tsv_path, arcname="validated.tsv")
            model = SimpleNamespace(
                transcribe=lambda **kwargs: [SimpleNamespace(text="سلام دنیا", language="Persian")]
            )
            report_path = root / "result.json"
            report = run_evaluation(archive_path, report_path, root / "cache", model=model)
            self.assertEqual(report["sample_count"], 1)
            self.assertEqual(report["wer"], 0.0)
            self.assertFalse(report["transcript_text_uploaded"])
            self.assertFalse(report["audio_uploaded"])
            serialized = report_path.read_text(encoding="utf-8").lower()
            self.assertNotIn("سلام دنیا", serialized)
            self.assertNotIn("private-client", serialized)
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "eval" / "issue47" / "qwen3_asr_persian.schema.json").read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(report)


if __name__ == "__main__":
    unittest.main()
