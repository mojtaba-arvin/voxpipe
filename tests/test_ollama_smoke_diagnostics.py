from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_ollama_structured_smoke.py"
SPEC = importlib.util.spec_from_file_location("run_ollama_structured_smoke", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class PartialStreamingResponse:
    def __enter__(self) -> PartialStreamingResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self):
        yield json.dumps(
            {"message": {"content": '{"events":'}, "done": False}
        ).encode("utf-8")
        raise TimeoutError("simulated interrupted model stream")


class SmokeDiagnosticsTests(unittest.TestCase):
    def test_partial_model_output_is_flushed_before_stream_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "prompt-injection.raw.partial.txt"
            SMOKE.CURRENT_SAMPLE_RAW_PATH = raw_path
            SMOKE.LAST_RAW_MODEL_OUTPUT = None
            payload = {
                "model": "qwen3:4b",
                "temperature": 0,
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "<EVIDENCE_CANDIDATES_JSON>\n"
                            '[{"span_id":"span-0001"}]'
                            "\n</EVIDENCE_CANDIDATES_JSON>"
                        ),
                    }
                ],
            }
            try:
                with patch.object(
                    SMOKE.urllib.request,
                    "urlopen",
                    return_value=PartialStreamingResponse(),
                ):
                    with self.assertRaisesRegex(TimeoutError, "interrupted"):
                        SMOKE.ollama_native_transport(
                            "http://127.0.0.1:11434/v1",
                            payload,
                            {},
                            120,
                        )
                self.assertEqual(raw_path.read_text(encoding="utf-8"), '{"events":')
                self.assertEqual(SMOKE.LAST_RAW_MODEL_OUTPUT, '{"events":')
            finally:
                SMOKE.CURRENT_SAMPLE_RAW_PATH = None
                SMOKE.LAST_RAW_MODEL_OUTPUT = None


if __name__ == "__main__":
    unittest.main()
