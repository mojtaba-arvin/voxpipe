from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from eval.issue47.persian_alignment import (
    MODEL_REVISION,
    _read_manifest,
    _validated_word_times,
    build_report,
    run_alignment,
)


class PersianAlignmentTests(unittest.TestCase):
    def test_pinned_model_revision_and_read_speech_boundary(self) -> None:
        self.assertEqual(MODEL_REVISION, "234714078a1398a9db88194c5a40fefe6f376dc1")
        report = build_report(
            clip_count=2,
            aligned_clip_count=1,
            failed_clip_count=1,
            word_count=10,
            aligned_word_count=8,
            model_dtype="float32",
            whisperx_version="3.8.6",
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["alignment_coverage"], 0.8)
        self.assertEqual(report["unaligned_word_count"], 2)
        self.assertEqual(report["timestamp_validation_failure_count"], 0)
        self.assertEqual(report["missing_word_timestamp_count"], 0)
        self.assertEqual(report["diarization_status"], "not_evaluated_read_speech")
        self.assertEqual(report["speaker_segment_count"], 0)
        self.assertFalse(report["alignment_intermediate_uploaded"])

    def test_word_timestamps_reject_non_finite_out_of_range_and_empty_words(self) -> None:
        words = [
            {"word": "سلام", "start": 0.1, "end": 0.5},
            {"word": "bad", "start": float("nan"), "end": 0.7},
            {"word": "late", "start": 1.1, "end": 1.2},
            {"word": "negative", "start": -0.1, "end": 0.2},
            {"word": "", "start": 0.2, "end": 0.3},
            {"word": "reverse", "start": 0.8, "end": 0.7},
        ]
        self.assertEqual(
            _validated_word_times(words, 1.0),
            [{"word": "سلام", "start": 0.1, "end": 0.5}],
        )

    def test_word_timestamps_must_be_monotonic_and_inside_audio(self) -> None:
        words = [
            {"word": "اول", "start": 0.1, "end": 0.3},
            {"word": "overlap", "start": 0.25, "end": 0.4},
            {"word": "دوم", "start": 0.3, "end": 0.5},
            {"word": "out-of-order", "start": 0.2, "end": 0.4},
            {"word": "past-end", "start": 0.9, "end": 1.01},
            {"word": "boolean", "start": True, "end": 0.8},
        ]
        self.assertEqual(
            _validated_word_times(words, 1.0),
            [
                {"word": "اول", "start": 0.1, "end": 0.3},
                {"word": "دوم", "start": 0.3, "end": 0.5},
            ],
        )

    def test_word_timestamp_overlap_tolerance_is_exactly_20_ms(self) -> None:
        accepted = _validated_word_times(
            [
                {"word": "اول", "start": 0.1, "end": 0.3},
                {"word": "دوم", "start": 0.28, "end": 0.5},
            ],
            1.0,
        )
        self.assertEqual(len(accepted), 2)
        rejected = _validated_word_times(
            [
                {"word": "اول", "start": 0.1, "end": 0.3},
                {"word": "دوم", "start": 0.2799, "end": 0.5},
            ],
            1.0,
        )
        self.assertEqual(len(rejected), 1)

    def test_word_timestamp_rejects_boolean_and_infinite_boundaries(self) -> None:
        words = [
            {"word": "valid", "start": 0.1, "end": 0.2},
            {"word": "boolean-start", "start": True, "end": 0.3},
            {"word": "boolean-end", "start": 0.2, "end": False},
            {"word": "positive-infinity", "start": 0.2, "end": float("inf")},
            {"word": "negative-infinity", "start": float("-inf"), "end": 0.3},
        ]
        self.assertEqual(
            _validated_word_times(words, 1.0),
            [{"word": "valid", "start": 0.1, "end": 0.2}],
        )

    def test_word_timestamp_validation_rejects_invalid_duration(self) -> None:
        for duration in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(ValueError, "finite and positive"):
                    _validated_word_times([], duration)

    def test_manifest_requires_existing_audio_and_non_empty_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "clip.wav"
            audio.write_bytes(b"private-audio")
            manifest = root / "input.jsonl"
            manifest.write_text(
                json.dumps({"audio_path": str(audio), "transcript": "سلام دنیا دوست"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(len(_read_manifest(manifest, 1)), 1)
            manifest.write_text(
                json.dumps({"audio_path": str(root / "missing.wav"), "transcript": "سلام"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "missing audio"):
                _read_manifest(manifest, 1)

    def test_alignment_report_counts_rejected_and_missing_word_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "clip.wav"
            audio.write_bytes(b"temporary-audio")
            manifest = root / "input.jsonl"
            manifest.write_text(
                json.dumps({"audio_path": str(audio), "transcript": "سلام دنیا دوست"}) + "\n",
                encoding="utf-8",
            )

            class Model:
                def parameters(self):
                    return iter([SimpleNamespace(dtype="torch.float32")])

            def align(_segments, _model, _metadata, _audio, _device, **_kwargs):
                return {
                    "segments": [{
                        "words": [
                            {"word": "سلام", "start": 0.1, "end": 0.4},
                            {"word": "دنیا", "start": float("nan"), "end": 0.8},
                        ]
                    }]
                }

            report = run_alignment(
                manifest,
                root / "alignment.json",
                root / "speakers.json",
                root / "cache",
                model_loader=lambda _cache: (Model(), {"language": "fa"}, align, lambda _path: [0.0] * 16000),
            )
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["clip_count"], 1)
            self.assertEqual(report["aligned_clip_count"], 1)
            self.assertEqual(report["failed_clip_count"], 0)
            self.assertEqual(report["aligned_word_count"], 1)
            self.assertEqual(report["unaligned_word_count"], 2)
            self.assertEqual(report["timestamp_validation_failure_count"], 1)
            self.assertEqual(report["missing_word_timestamp_count"], 1)
            self.assertEqual(report["alignment_coverage"], round(1 / 3, 6))
            speaker_report = json.loads((root / "speakers.json").read_text(encoding="utf-8"))
            self.assertEqual(speaker_report["diarization_status"], "not_evaluated_read_speech")
            self.assertEqual(speaker_report["speaker_segments"], [])

    def test_build_report_rejects_invalid_timestamp_failure_counts(self) -> None:
        for value in (-1, 11, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "timestamp failure counts"):
                    build_report(
                        clip_count=1,
                        aligned_clip_count=1,
                        failed_clip_count=0,
                        word_count=10,
                        aligned_word_count=5,
                        model_dtype="float32",
                        whisperx_version="3.8.6",
                        timestamp_validation_failure_count=value,
                    )

    def test_report_rejects_unaccounted_clips(self) -> None:
        with self.assertRaisesRegex(ValueError, "account for every clip"):
            build_report(
                clip_count=3,
                aligned_clip_count=1,
                failed_clip_count=1,
                word_count=10,
                aligned_word_count=5,
                model_dtype="float32",
                whisperx_version="3.8.6",
            )


if __name__ == "__main__":
    unittest.main()
