import unittest

from scripts.run_ollama_structured_smoke import (
    candidate_schema_constraints,
    ollama_output_schema,
    required_event_fields_for_candidates,
)


class EvidenceDerivedSmokeSchemaTests(unittest.TestCase):
    def schema(self, quote):
        candidates = [{"span_id": "span-1", "quote": quote}]
        constraints = candidate_schema_constraints(candidates)
        schema = ollama_output_schema(
            ("span-1",), required_event_fields_for_candidates(candidates),
            **constraints,
        )
        return constraints, schema

    def test_meeting_schema_requires_full_title_and_explicit_topic(self):
        constraints, schema = self.schema(
            "جلسه بودجه فردا ساعت ۹ در دفتر مرکزی برگزار می‌شود."
        )
        self.assertEqual(constraints["allowed_event_names"], ("جلسه بودجه",))
        self.assertEqual(constraints["allowed_topic_names"], ("بودجه",))
        self.assertEqual(constraints["max_relations"], 0)
        props = schema["properties"]
        self.assertEqual(props["topics"]["minItems"], 1)
        self.assertEqual(props["topics"]["maxItems"], 1)
        self.assertEqual(
            props["topics"]["items"]["properties"]["value"]["properties"]["name"]["enum"],
            ["بودجه"],
        )
        self.assertEqual(
            props["events"]["items"]["properties"]["value"]["properties"]["name"]["enum"],
            ["جلسه بودجه"],
        )

    def test_reporting_meeting_does_not_authorize_topic_or_relation(self):
        constraints, schema = self.schema("مریم گفت جلسه فردا ساعت ۹ برگزار می‌شود.")
        self.assertEqual(constraints["allowed_event_names"], ("جلسه",))
        self.assertEqual(constraints["max_topics"], 0)
        self.assertEqual(constraints["required_topics"], 0)
        self.assertEqual(constraints["max_relations"], 0)
        self.assertEqual(schema["properties"]["topics"]["maxItems"], 0)
        self.assertEqual(schema["properties"]["relations"]["maxItems"], 0)

    def test_contract_schema_requires_source_grounded_event_topic_and_relation(self):
        constraints, schema = self.schema("شرکت آریا با شرکت بهار قرارداد همکاری امضا کرد.")
        self.assertEqual(constraints["allowed_event_names"], ("امضای قرارداد همکاری",))
        self.assertEqual(constraints["allowed_topic_names"], ("قرارداد همکاری",))
        self.assertEqual(constraints["max_relations"], 1)
        self.assertEqual(constraints["required_relations"], 1)
        self.assertEqual(schema["properties"]["topics"]["minItems"], 1)
        self.assertEqual(schema["properties"]["relations"]["minItems"], 1)

    def test_action_obligation_requires_relation_and_action_without_topic(self):
        constraints, schema = self.schema("علی باید گزارش فروش را تا جمعه ارسال کند.")
        self.assertEqual(constraints["max_topics"], 0)
        self.assertEqual(constraints["max_relations"], 1)
        self.assertEqual(constraints["required_relations"], 1)
        self.assertEqual(constraints["max_action_items"], 32)
        self.assertEqual(schema["properties"]["topics"]["maxItems"], 0)


if __name__ == "__main__":
    unittest.main()
