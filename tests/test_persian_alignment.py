from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eval.issue47.persian_alignment import (
    MODEL_REVISION,
    _read_manifest,
    _validated_word_times,
    build_report,
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
                json.dumps({"audio_path": str(audio), "transcript": "سلام دنیا"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(len(_read_manifest(manifest, 1)), 1)
            manifest.write_text(
                json.dumps({"audio_path": str(root / "missing.wav"), "transcript": "سلام"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "missing audio"):
                _read_manifest(manifest, 1)

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
