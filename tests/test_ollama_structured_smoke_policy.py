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


    def test_contract_relation_triple_is_derived_from_signed_object(self):
        constraints, schema = self.schema("شرکت آریا با شرکت بهار قرارداد همکاری امضا کرد.")
        self.assertEqual(constraints["allowed_relation_triples"], ({
            "subject": "شرکت آریا", "predicate": "امضا کرد", "object": "قرارداد همکاری با شرکت بهار",
        },))
        relation = schema["properties"]["relations"]["items"]["properties"]["value"]["properties"]
        self.assertEqual(relation["subject"]["enum"], ["شرکت آریا"])
        self.assertEqual(relation["predicate"]["enum"], ["امضا کرد"])
        self.assertEqual(relation["object"]["enum"], ["قرارداد همکاری با شرکت بهار"])

    def test_action_relation_triple_preserves_obligation_and_object_boundaries(self):
        constraints, schema = self.schema("علی باید گزارش فروش را تا جمعه ارسال کند.")
        self.assertEqual(constraints["allowed_relation_triples"], ({
            "subject": "علی", "predicate": "باید ارسال کند", "object": "گزارش فروش",
        },))
        relation = schema["properties"]["relations"]["items"]["properties"]["value"]["properties"]
        self.assertEqual(relation["subject"]["enum"], ["علی"])
        self.assertEqual(relation["predicate"]["enum"], ["باید ارسال کند"])
        self.assertEqual(relation["object"]["enum"], ["گزارش فروش"])


    def test_entity_candidates_are_exact_and_required_for_each_sample(self):
        action, action_schema = self.schema("علی باید گزارش فروش را تا جمعه ارسال کند.")
        self.assertEqual(action["allowed_entities"], (
            {"name": "علی", "entity_type": "person"},
            {"name": "گزارش فروش", "entity_type": "document"},
        ))
        action_entities = action_schema["properties"]["entities"]
        self.assertEqual(action_entities["minItems"], 2)
        self.assertEqual(action_entities["maxItems"], 2)

        meeting, meeting_schema = self.schema(
            "جلسه بودجه فردا ساعت ۹ در دفتر مرکزی برگزار می‌شود."
        )
        self.assertEqual(meeting["allowed_entities"], (
            {"name": "دفتر مرکزی", "entity_type": "location"},
        ))
        self.assertEqual(meeting_schema["properties"]["entities"]["minItems"], 1)

        contract, contract_schema = self.schema("شرکت آریا با شرکت بهار قرارداد همکاری امضا کرد.")
        self.assertEqual(contract["allowed_entities"], (
            {"name": "شرکت آریا", "entity_type": "organization"},
            {"name": "شرکت بهار", "entity_type": "organization"},
        ))
        self.assertEqual(contract_schema["properties"]["entities"]["minItems"], 2)


if __name__ == "__main__":
    unittest.main()
