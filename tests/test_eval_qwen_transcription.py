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

    @staticmethod
    def _archive_with_durations(root: Path, durations: list[float]) -> Path:
        archive_path = root / "duration-sample.tar.gz"
        tsv_path = root / "validated.tsv"
        rows = ["client_id\tpath\tsentence"]
        with tarfile.open(archive_path, "w:gz") as archive:
            for index, duration in enumerate(durations, start=1):
                name = f"clip-{index}.wav"
                audio_path = root / name
                with wave.open(str(audio_path), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(16000)
                    audio.writeframes(b"\x00\x00" * int(16000 * duration))
                archive.add(audio_path, arcname=f"clips/{name}")
                rows.append(f"private-client\t{name}\tسلام دنیا")
            tsv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            archive.add(tsv_path, arcname="validated.tsv")
        return archive_path

    def test_duration_filter_selects_first_eligible_clips_before_language_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive_with_durations(root, [0.5, 1.25, 2.0])
            prediction = SimpleNamespace(
                language="fa", confidence=0.99, raw_language="fa", abstention_reason=None
            )
            seen_audio_paths = []

            def transcribe(_model, _processor, audio_path, *, language):
                self.assertEqual(language, "Persian")
                seen_audio_paths.append(audio_path.name)
                return {"language": "Persian", "transcription": "سلام دنیا"}

            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction) as identify, \
                 patch("eval.issue47.qwen_transcription._transcribe_audio", side_effect=transcribe):
                report = run_evaluation(
                    archive_path, root / "result.json", root / "cache", max_clips=2,
                    candidate_scan_limit=10,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(),
                )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["selected_sample_count"], 2)
            self.assertEqual(report["duration_shortfall_count"], 0)
            self.assertEqual(report["candidate_rows_scanned"], 3)
            self.assertEqual(report["duration_rejected_count"], 1)
            self.assertEqual(report["duration_probe_failures"], 0)
            self.assertEqual(report["min_audio_duration_seconds"], 1.0)
            self.assertEqual(seen_audio_paths, ["candidate-0002.wav", "candidate-0003.wav"])
            self.assertEqual(identify.call_count, 2)

    def test_duration_shortfall_fails_without_metrics_on_reduced_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive_with_durations(root, [0.5, 0.8])
            with patch("eval.issue47.qwen_transcription._identify_audio_language") as identify, \
                 patch("eval.issue47.qwen_transcription._transcribe_audio") as transcribe:
                report = run_evaluation(
                    archive_path, root / "shortfall.json", root / "cache", max_clips=2,
                    candidate_scan_limit=10,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(),
                )
            identify.assert_not_called()
            transcribe.assert_not_called()
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["selected_sample_count"], 0)
            self.assertEqual(report["duration_shortfall_count"], 2)
            self.assertEqual(report["duration_rejected_count"], 2)
            self.assertEqual(report["sample_count"], 0)
            self.assertIsNone(report["wer"])
            self.assertIsNone(report["cer"])
            self.assertIsNone(report["real_time_factor"])

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

    def test_low_confidence_persian_top_label_is_rejected_before_qwen_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            prediction = SimpleNamespace(
                language="und", confidence=0.49, raw_language="fa",
                abstention_reason="low_confidence",
            )
            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction), \
                 patch("eval.issue47.qwen_transcription._transcribe_audio") as transcribe, \
                 patch(
                     "eval.issue47.qwen_transcription.build_report",
                     return_value={
                         "status": "failed",
                         "language_id_failures": {"arabic": 0, "other_or_undetermined": 1},
                         "wer": None,
                         "cer": None,
                     },
                 ) as build_report:
                report = run_evaluation(
                    archive_path, root / "low-confidence.json", root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(), language_id_confidence_threshold=0.5,
                )
            transcribe.assert_not_called()
            self.assertEqual(build_report.call_args.kwargs["language_id_confidence_threshold"], 0.5)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["language_id_failures"]["other_or_undetermined"], 1)
            self.assertIsNone(report["wer"])
            self.assertIsNone(report["cer"])

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
                    with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction), \
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

    @staticmethod
    def _archive_with_short_and_valid_clips(root: Path) -> Path:
        archive_path = root / "duration-filtered.tar.gz"
        short_path = root / "short.wav"
        valid_path = root / "valid.wav"
        for path, frames in ((short_path, 8000), (valid_path, 24000)):
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * frames)
        tsv_path = root / "validated.tsv"
        tsv_path.write_text(
            "client_id\tpath\tsentence\nprivate-a\tshort.wav\tکوتاه\nprivate-b\tvalid.wav\tسلام دنیا\n",
            encoding="utf-8",
        )
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(short_path, arcname="clips/short.wav")
            archive.add(valid_path, arcname="clips/valid.wav")
            archive.add(tsv_path, arcname="validated.tsv")
        return archive_path

    def test_short_clips_are_excluded_before_language_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive_with_short_and_valid_clips(root)
            prediction = SimpleNamespace(
                language="fa", confidence=0.99, raw_language="fa", abstention_reason=None
            )
            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction) as identify, \
                 patch("eval.issue47.qwen_transcription._transcribe_audio", return_value={"language": "Persian", "transcription": "سلام دنیا"}) as transcribe:
                report = run_evaluation(
                    archive_path, root / "duration.json", root / "cache", max_clips=1,
                    candidate_scan_limit=2, model=SimpleNamespace(dtype="float32"),
                    processor=object(), language_identifier=object(),
                )
            self.assertEqual(identify.call_count, 1)
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["selected_sample_count"], 1)
            self.assertEqual(report["candidate_rows_scanned"], 2)
            self.assertEqual(report["duration_rejected_count"], 1)
            self.assertEqual(report["duration_shortfall_count"], 0)
            self.assertEqual(report["wer"], 0.0)
            self.assertEqual(report["cer"], 0.0)
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "eval" / "issue47" / "qwen3_asr_persian.schema.json").read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(report)

    def test_duration_shortfall_suppresses_wer_and_cer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive_with_short_and_valid_clips(root)
            report = run_evaluation(
                archive_path, root / "shortfall.json", root / "cache", max_clips=2,
                candidate_scan_limit=1,
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["selected_sample_count"], 0)
            self.assertEqual(report["duration_shortfall_count"], 2)
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

    def test_alignment_manifest_contains_exact_audio_and_qwen_hypothesis_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            intermediate_dir = root / "alignment-input"
            language_prediction = SimpleNamespace(
                language="fa", confidence=0.99, raw_language="fa", abstention_reason=None
            )
            forced_hypothesis = {"language": "Persian", "transcription": "سلام جهان"}
            seen_audio_paths = []

            def transcribe(_model, _processor, audio_path, *, language):
                self.assertEqual(language, "Persian")
                seen_audio_paths.append(audio_path.resolve())
                return forced_hypothesis

            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=language_prediction), \
                 patch("eval.issue47.qwen_transcription._transcribe_audio", side_effect=transcribe):
                report = run_evaluation(
                    archive_path, root / "result.json", root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(), intermediate_dir=intermediate_dir,
                )

            manifest = intermediate_dir / "alignment_input.jsonl"
            self.assertEqual(report["status"], "passed")
            self.assertTrue(manifest.is_file())
            rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), {"audio_path", "transcript"})
            self.assertEqual(rows[0]["transcript"], "سلام جهان")
            self.assertNotEqual(rows[0]["transcript"], "سلام دنیا")
            self.assertEqual(Path(rows[0]["audio_path"]), seen_audio_paths[0])
            self.assertTrue(seen_audio_paths[0].is_file())

    def test_incomplete_language_gate_does_not_create_alignment_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            intermediate_dir = root / "alignment-input"
            prediction = SimpleNamespace(
                language="ar", confidence=0.99, raw_language="ar", abstention_reason=None
            )
            with patch("eval.issue47.qwen_transcription._identify_audio_language", return_value=prediction), \
                 patch("eval.issue47.qwen_transcription._transcribe_audio") as transcribe:
                report = run_evaluation(
                    archive_path, root / "rejected.json", root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(), intermediate_dir=intermediate_dir,
                )
            transcribe.assert_not_called()
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["wer"])
            self.assertFalse((intermediate_dir / "alignment_input.jsonl").exists())

    def test_alignment_intermediate_directory_must_be_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = self._archive(root)
            intermediate_dir = root / "alignment-input"
            intermediate_dir.mkdir()
            (intermediate_dir / "unexpected.txt").write_text("private", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be empty"):
                run_evaluation(
                    archive_path, root / "result.json", root / "cache", max_clips=1,
                    model=SimpleNamespace(dtype="float32"), processor=object(),
                    language_identifier=object(), intermediate_dir=intermediate_dir,
                )

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
            self.assertEqual(report["selected_sample_count"], 1)
            self.assertEqual(report["duration_shortfall_count"], 0)
            self.assertEqual(report["candidate_rows_scanned"], 1)
            self.assertEqual(report["duration_rejected_count"], 0)
            self.assertEqual(report["min_audio_duration_seconds"], 1.0)
            self.assertEqual(report["wer"], 0.0)
            self.assertEqual(
                report["language_id_model_revision"],
                "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9",
            )
            self.assertEqual(report["language_id_confidence_threshold"], 0.5)
            self.assertEqual(
                report["language_id_threshold_policy"],
                "validation_calibrated_minimum_confidence_floor_0_5_top_label_fa_with_zero_and_tie_abstention",
            )
            held_out = report["language_id_held_out_control"]
            self.assertEqual(held_out["sample_count"], 400)
            self.assertEqual(held_out["persian_correct"], 100)
            self.assertEqual(held_out["arabic_correct"], 99)
            self.assertEqual(held_out["persian_arabic_cross_confusions"], 0)
            self.assertEqual(held_out["selective_accuracy"], 1.0)
            self.assertEqual(held_out["coverage"], 0.9225)
            self.assertEqual(held_out["negative_control_abstentions"], 30)
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
