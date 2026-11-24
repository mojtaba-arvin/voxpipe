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
        self.assertEqual(
            entity_value["oneOf"],
            [{
                "type": "object", "additionalProperties": False, "required": ["name", "entity_type"],
                "properties": {
                    "name": {"const": "دفتر مرکزی"},
                    "entity_type": {"const": "location"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                },
            }],
        )
        self.assertEqual(schema["properties"]["events"]["maxItems"], 32)
        self.assertEqual(schema["properties"]["events"]["items"]["properties"]["value"]["properties"]["event_type"]["enum"], ["meeting", "contract-signing"])

    def test_action_deadline_cannot_generate_an_event(self) -> None:
        candidates = [{"span_id": "span-0001", "quote": "علی باید گزارش را تا جمعه ارسال کند."}]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["max_events"], 0)
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        self.assertEqual(schema["properties"]["events"]["maxItems"], 0)

    def test_unrelated_action_deadline_does_not_require_event_time(self) -> None:
        candidates = [
            {"span_id": "span-0001", "quote": "جلسه برگزار می‌شود."},
            {"span_id": "span-0002", "quote": "علی باید گزارش را فردا ارسال کند."},
        ]
        self.assertEqual(SMOKE.required_event_fields_for_candidates(candidates), ())

    def test_event_time_and_location_are_required_from_the_same_candidate(self) -> None:
        candidates = [
            {"span_id": "span-0001", "quote": "جلسه برگزار می‌شود."},
            {"span_id": "span-0002", "quote": "جلسه فردا ساعت ۹ در دفتر مرکزی برگزار می‌شود."},
        ]
        self.assertEqual(
            SMOKE.required_event_fields_for_candidates(candidates),
            ("start_time", "location"),
        )

    def test_reporting_speaker_alone_cannot_generate_an_entity(self) -> None:
        candidates = [{"span_id": "span-0001", "quote": "مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود."}]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["max_entities"], 0)
        self.assertEqual(constraints["max_events"], 1)
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
        self.assertEqual(event_value["properties"]["name"]["enum"], ["امضای قرارداد همکاری"])

    def test_entity_types_are_scoped_to_explicit_candidate_cues(self) -> None:
        candidates = [
            {"span_id": "span-0001", "quote": "جلسه بودجه فردا ساعت ۹ در دفتر مرکزی برگزار می‌شود."}
        ]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["allowed_entity_types"], ("location",))
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        entity_value = schema["properties"]["entities"]["items"]["properties"]["value"]
        self.assertEqual(entity_value["properties"]["entity_type"]["enum"], ["location"])
        self.assertEqual(schema["properties"]["entities"]["maxItems"], 32)

    def test_action_item_fields_are_constrained_by_explicit_obligation(self) -> None:
        candidates = [
            {"span_id": "span-0001", "quote": "علی باید گزارش فروش را تا جمعه ارسال کند."}
        ]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertEqual(constraints["allowed_entity_types"], ("person", "document"))
        self.assertEqual(constraints["required_action_item_fields"], ("assignee", "due_date"))
        self.assertFalse(constraints["allow_action_item_status"])
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        action = schema["properties"]["action_items"]
        self.assertEqual(action["maxItems"], 32)
        value = action["items"]["properties"]["value"]
        self.assertEqual(value["required"], ["description", "assignee", "due_date"])
        self.assertNotIn("status", value["properties"])
        pattern = value["properties"]["description"]["pattern"]
        self.assertIsNone(__import__("re").search(pattern, "ارسال گزارش فروش تا جمعه"))
        self.assertIsNotNone(__import__("re").search(pattern, "ارسال گزارش فروش"))

    def test_action_status_is_allowed_only_when_source_states_a_status(self) -> None:
        candidates = [
            {"span_id": "span-0001", "quote": "علی باید گزارش را ارسال کند و کار انجام شد."}
        ]
        constraints = SMOKE.candidate_schema_constraints(candidates)
        self.assertTrue(constraints["allow_action_item_status"])
        schema = SMOKE.ollama_output_schema(["span-0001"], **constraints)
        value = schema["properties"]["action_items"]["items"]["properties"]["value"]
        self.assertIn("status", value["properties"])

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
