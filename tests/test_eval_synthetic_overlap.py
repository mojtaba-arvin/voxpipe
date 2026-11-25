from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from eval.issue47.synthetic_overlap import (
    VOICE_MODELS,
    build_annotation,
    calculate_overlap_window,
    mix_samples,
)

ROOT = Path(__file__).resolve().parents[1]


class SyntheticOverlapTests(unittest.TestCase):
    def test_overlap_window_is_exact_and_non_empty(self) -> None:
        delay, end, duration = calculate_overlap_window(4.0, 3.0)
        self.assertEqual(delay, 0.75)
        self.assertEqual(end, 3.75)
        self.assertEqual(duration, 3.0)

    def test_overlap_window_rejects_too_short_utterances(self) -> None:
        with self.assertRaisesRegex(ValueError, "too short"):
            calculate_overlap_window(1.0, 0.1)

    def test_mix_samples_offsets_second_stream_and_limits_peak(self) -> None:
        mixed, leading_samples, delay_samples = mix_samples(
            [0.8] * 8, [0.8] * 8, 4, 0.5,
            leading_silence_seconds=0.5, noise_snr_db=None,
        )
        self.assertEqual(leading_samples, 2)
        self.assertEqual(delay_samples, 2)
        self.assertEqual(len(mixed), 12)
        self.assertEqual(list(mixed[:2]), [0.0, 0.0])
        self.assertAlmostEqual(max(mixed), 0.98, places=5)

    def test_noise_injection_is_seeded(self) -> None:
        first = mix_samples([0.2] * 8, [0.1] * 8, 4, 0.5, noise_seed=42)[0]
        second = mix_samples([0.2] * 8, [0.1] * 8, 4, 0.5, noise_seed=42)[0]
        self.assertEqual(list(first), list(second))

    def test_annotations_contain_roles_and_ground_truth_overlap_only(self) -> None:
        conversation = {
            "conversation_id": "conversation-0001",
            "scenario": "refund",
        }
        annotation = build_annotation(conversation, 4.0, 3.0)
        self.assertEqual(annotation["source_kind"], "synthetic_template")
        self.assertEqual(annotation["leading_silence_seconds"], 0.25)
        self.assertEqual(annotation["noise_snr_db"], 30.0)
        self.assertEqual(annotation["overlap"]["start_seconds"], 1.0)
        self.assertEqual(annotation["overlap"]["end_seconds"], 4.0)
        self.assertEqual(annotation["overlap"]["duration_seconds"], 3.0)
        self.assertEqual(annotation["segments"][0]["role"], "customer")
        self.assertEqual(annotation["segments"][1]["role"], "agent")
        self.assertEqual(annotation["segments"][0]["voice_model"], VOICE_MODELS["customer"]["name"])
        schema = json.loads((ROOT / "eval" / "issue47" / "overlap_annotation.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(annotation)


if __name__ == "__main__":
    unittest.main()
