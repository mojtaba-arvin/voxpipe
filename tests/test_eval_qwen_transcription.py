from __future__ import annotations

import json
import sys
import tarfile
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch
import wave
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from eval.issue47.qwen_transcription import (
    _parse_persian_transcription,
    _transcribe_audio,
    corpus_error_rates,
    run_evaluation,
)


class QwenPersianEvaluationTests(unittest.TestCase):
    def test_persian_language_contract_rejects_non_persian_and_empty_output(self) -> None:
        self.assertEqual(
            _parse_persian_transcription({"language": "Persian", "transcription": "سلام دنیا"}),
            "سلام دنیا",
        )
        with self.assertRaisesRegex(RuntimeError, "unexpected language"):
            _parse_persian_transcription({"language": "Arabic", "transcription": "مرحبا"})
        with self.assertRaisesRegex(RuntimeError, "empty Persian transcription"):
            _parse_persian_transcription({"language": "Persian", "transcription": " "})

    def test_qwen_transcription_request_is_explicitly_forced_to_persian(self) -> None:
        class InputIds:
            shape = (1, 2)

            def __getitem__(self, key):
                return self

        class Inputs(dict):
            def to(self, *args):
                return self

        class OutputIds:
            def __getitem__(self, key):
                return self

        class Processor:
            def __init__(self):
                self.request = None

            def apply_transcription_request(self, **kwargs):
                self.request = kwargs
                return Inputs(input_ids=InputIds())

            def decode(self, generated_ids, *, return_format):
                self.assert_format = return_format
                return ["سلام دنیا"]

        class Model:
            dtype = "float32"

            def generate(self, **kwargs):
                self.kwargs = kwargs
                return OutputIds()

        processor = Processor()
        model = Model()
        with patch.dict(sys.modules, {"torch": SimpleNamespace(inference_mode=nullcontext)}):
            result = _transcribe_audio(model, processor, Path("clip.wav"))
        self.assertEqual(processor.request["language"], "Persian")
        self.assertEqual(processor.assert_format, "transcription_only")
        self.assertEqual(result, {"language": "Persian", "transcription": "سلام دنیا"})
        with self.assertRaisesRegex(ValueError, "only permits forced Persian"):
            _transcribe_audio(model, processor, Path("clip.wav"), language="Arabic")

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

    @staticmethod
    def _archive(root: Path) -> Path:
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
        return archive_path

    def test_arabic_audio_is_rejected_without_publishing_wer_or_cer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            prediction = SimpleNamespace(
                language="ar", confidence=0.99, raw_language="ar", abstention_reason=None
            )
            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction), \
                 patch("eval.issue47.qwen_transcription._transcribe_audio") as transcribe:
                report = run_evaluation(
                    archive_path, root / "rejected.json", root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(),
                )
            transcribe.assert_not_called()
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["language_id_failures"]["arabic"], 1)
            self.assertIsNone(report["wer"])
            self.assertIsNone(report["cer"])
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "eval" / "issue47" / "qwen3_asr_persian.schema.json").read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(report)

    def test_out_of_inventory_and_undetermined_audio_are_rejected(self) -> None:
        cases = (
            SimpleNamespace(language="und", confidence=0.91, raw_language="en", abstention_reason="out_of_inventory"),
            SimpleNamespace(language="und", confidence=0.0, raw_language="fa", abstention_reason="low_confidence"),
        )
        for prediction in cases:
            with self.subTest(raw_language=prediction.raw_language, reason=prediction.abstention_reason):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    archive_path = self._archive(root)
                    with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction), \\
                         patch("eval.issue47.qwen_transcription._transcribe_audio") as transcribe:
                        report = run_evaluation(
                            archive_path, root / "rejected.json", root / "cache", max_clips=1,
                            model=SimpleNamespace(dtype="float32"), processor=object(),
                            language_identifier=object(),
                        )
                    transcribe.assert_not_called()
                    self.assertEqual(report["status"], "failed")
                    self.assertEqual(report["language_id_failures"]["other_or_undetermined"], 1)
                    self.assertIsNone(report["wer"])
                    self.assertIsNone(report["cer"])

    def test_empty_hypothesis_is_counted_and_suppresses_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            prediction = SimpleNamespace(
                language="fa", confidence=0.99, raw_language="fa", abstention_reason=None
            )
            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction), \
                 patch("eval.issue47.qwen_transcription._transcribe_audio", return_value={"language": "Persian", "transcription": ""}):
                report = run_evaluation(
                    archive_path, root / "empty.json", root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(),
                )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["transcription_failures"]["empty_output"], 1)
            self.assertIsNone(report["wer"])
            self.assertIsNone(report["cer"])

    def test_pipeline_writes_aggregate_only_report_and_cleans_temporary_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            report_path = root / "result.json"
            language_prediction = SimpleNamespace(
                language="fa", confidence=0.99, raw_language="fa", abstention_reason=None
            )
            forced_hypothesis = {"language": "Persian", "transcription": "سلام دنیا"}
            seen_audio_paths = []

            def transcribe(_model, _processor, audio_path, *, language):
                self.assertEqual(language, "Persian")
                seen_audio_paths.append(audio_path)
                return forced_hypothesis

            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=language_prediction), \
                 patch("eval.issue47.qwen_transcription._transcribe_audio", side_effect=transcribe):
                report = run_evaluation(
                    archive_path, report_path, root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(),
                )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["sample_count"], 1)
            self.assertEqual(report["requested_sample_count"], 1)
            self.assertEqual(report["wer"], 0.0)
            self.assertEqual(
                report["language_id_model_revision"],
                "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9",
            )
            self.assertEqual(report["language_id_confidence_threshold"], 0.0)
            self.assertEqual(report["forced_language"], "Persian")
            self.assertEqual(report["max_new_tokens"], 256)
            self.assertFalse(report["do_sample"])
            self.assertEqual(report["batch_size"], 1)
            self.assertEqual(report["language_id_sample_rate"], 16000)
            self.assertTrue(report["speechbrain_version"])
            self.assertTrue(report["torchaudio_version"])
            self.assertTrue(report["huggingface_hub_version"])
            self.assertFalse(report["transcript_text_uploaded"])
            self.assertFalse(report["audio_uploaded"])
            self.assertEqual(len(seen_audio_paths), 1)
            self.assertFalse(seen_audio_paths[0].exists())
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
