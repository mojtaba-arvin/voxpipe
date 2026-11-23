from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_fleurs_lid_benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_fleurs_lid_benchmark", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class FakeDataset:
    def __init__(self, rows):
        self.rows = rows

    def cast_column(self, name, audio_type):
        self.cast = (name, audio_type)
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeArray(list):
    ndim = 1

    def tolist(self):
        return list(self)


class FakeNumpy:
    float32 = object()

    @staticmethod
    def asarray(values, dtype=None):
        return FakeArray(values)


class FakeSoundFile:
    @staticmethod
    def read(stream, dtype, always_2d):
        return [0.0] * 16000, 16000


class FakeAudio:
    def __init__(self, decode):
        self.decode = decode


class FakeModel:
    model_version = "model-sha"

    def identify(self, waveform, *, sample_rate):
        return SimpleNamespace(language="fa", confidence=0.9)


class LanguageIdBenchmarkSplitTests(unittest.TestCase):
    def test_model_revision_is_pinned_for_reproducible_inference(self):
        self.assertEqual(
            BENCHMARK.MODEL_REVISION,
            "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9",
        )

    def test_load_split_uses_requested_frozen_split_and_checks_per_language_count(self):
        calls = []

        def dataset_loader(kind, *, data_files, split, streaming):
            calls.append((kind, data_files, split, streaming))
            return FakeDataset([{"audio": {"bytes": b"audio-fixture"}, "id": 7}])

        references, predictions, rows = BENCHMARK.load_split(
            "dev", 1, FakeModel(), dataset_loader=dataset_loader, audio_type=FakeAudio,
            soundfile=FakeSoundFile, numpy=FakeNumpy,
        )
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[2] == "dev" and call[3] is True for call in calls))
        self.assertTrue(all("/dev-00000-of-00001.parquet" in call[1]["dev"] for call in calls))
        self.assertEqual(len(references), 3)
        self.assertEqual(len(predictions), 3)
        self.assertTrue(all(row["dataset_split"] == "dev" for row in rows))
        self.assertTrue(all(row["model_revision"] == "model-sha" for row in rows))

    def test_load_split_rejects_missing_audio_bytes(self):
        def dataset_loader(kind, *, data_files, split, streaming):
            return FakeDataset([{"audio": {"bytes": None}, "id": 1}])

        with self.assertRaisesRegex(RuntimeError, "missing raw audio bytes"):
            BENCHMARK.load_split(
                "test", 1, FakeModel(), dataset_loader=dataset_loader, audio_type=FakeAudio,
                soundfile=FakeSoundFile, numpy=FakeNumpy,
            )

    def test_run_rejects_invalid_calibration_thresholds_before_loading_optional_models(self):
        with self.assertRaises(ValueError):
            BENCHMARK.run(1, Path("unused"), threshold=float("nan"))
        with self.assertRaises(ValueError):
            BENCHMARK.run(1, Path("unused"), calibration_samples=0)
        with self.assertRaises(ValueError):
            BENCHMARK.run(1, Path("unused"), min_coverage=1.1)


if __name__ == "__main__":
    unittest.main()
