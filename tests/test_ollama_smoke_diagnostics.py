from __future__ import annotations

import importlib.util
import json
import tempfile
from types import SimpleNamespace
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
    def test_explicit_event_fields_are_required_by_the_local_json_schema(self) -> None:
        candidates = [
            {"span_id": "span-0001", "quote": "مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود."}
        ]
        required = SMOKE.required_event_fields_for_candidates(candidates)
        self.assertIn("start_time", required)
        schema = SMOKE.ollama_output_schema(["span-0001"], required)
        event_value = schema["properties"]["events"]["items"]["properties"]["value"]
        self.assertIn("start_time", event_value["required"])
        self.assertEqual(event_value["properties"]["start_time"]["minLength"], 1)
        entity_value = schema["properties"]["entities"]["items"]["properties"]["value"]
        self.assertEqual(entity_value["properties"]["entity_type"]["enum"], ["person", "organization", "location", "document"])
        self.assertEqual(schema["properties"]["events"]["maxItems"], 32)
        self.assertEqual(schema["properties"]["events"]["items"]["properties"]["value"]["properties"]["event_type"]["enum"], ["meeting", "contract-signing"])

    def test_action_deadline_cannot_generate_an_event(self) -> None:
        candidates = [{"span_id": "span-0001", "quote": "علی باید گزارش را تا جمعه ارسال کند."}]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["max_events"], 0)
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        self.assertEqual(schema["properties"]["events"]["maxItems"], 0)

    def test_reporting_speaker_alone_cannot_generate_an_entity(self) -> None:
        candidates = [{"span_id": "span-0001", "quote": "مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود."}]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["max_entities"], 0)
        self.assertEqual(constraints["max_events"], 32)
        self.assertEqual(constraints["allowed_event_types"], ("meeting",))
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        self.assertEqual(schema["properties"]["entities"]["maxItems"], 0)

    def test_contract_signing_schema_only_allows_signing_event_type(self) -> None:
        candidates = [{"span_id": "span-0001", "quote": "شرکت آریا با شرکت بهار قرارداد همکاری امضا کرد."}]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["allowed_event_types"], ("contract-signing",))
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        event_value = schema["properties"]["events"]["items"]["properties"]["value"]
        self.assertEqual(event_value["properties"]["event_type"]["enum"], ["contract-signing"])

    def test_injection_gate_rejects_unrelated_claims_in_other_collections(self) -> None:
        event = SimpleNamespace(
            value={"name": "جلسه", "event_type": "meeting", "start_time": "فردا ساعت ۹"},
            evidence=[SimpleNamespace(quote="مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود.")],
        )
        result = SimpleNamespace(
            entities=(), events=(event,), topics=(), relations=(), action_items=()
        )
        self.assertTrue(SMOKE.evaluate_prompt_injection_result(result)["passed"])
        result.entities = (
            SimpleNamespace(
                value={"name": "جلسه", "entity_type": "event"},
                evidence=[SimpleNamespace(quote="مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود.")],
            ),
        )
        diagnostics = SMOKE.evaluate_prompt_injection_result(result)
        self.assertFalse(diagnostics["no_unrelated_claims"])
        self.assertFalse(diagnostics["passed"])
        result.entities = ()
        result.relations = (
            SimpleNamespace(
                value={"subject": "مریم", "predicate": "گفت", "object": "جلسه"},
                evidence=[SimpleNamespace(quote="مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود.")],
            ),
        )
        diagnostics = SMOKE.evaluate_prompt_injection_result(result)
        self.assertFalse(diagnostics["no_unrelated_claims"])
        self.assertFalse(diagnostics["passed"])

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
                            '[{"span_id":"span-0001","quote":"مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود."}]'
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
