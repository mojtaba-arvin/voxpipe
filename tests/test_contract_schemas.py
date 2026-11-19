"""Schema and fixture tests for public/internal VoxPipe contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
EXAMPLES = ROOT / "contracts" / "examples"


class ContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.asset_schema = json.loads(
            (SCHEMAS / "audio-asset.v1.schema.json").read_text(encoding="utf-8")
        )
        cls.session_schema = json.loads(
            (SCHEMAS / "ingestion-session.v1.schema.json").read_text(encoding="utf-8")
        )
        cls.transcript_schema = json.loads(
            (SCHEMAS / "transcript.v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(cls.asset_schema)
        Draft202012Validator.check_schema(cls.session_schema)
        Draft202012Validator.check_schema(cls.transcript_schema)
        cls.asset_validator = Draft202012Validator(
            cls.asset_schema, format_checker=FormatChecker()
        )
        cls.session_validator = Draft202012Validator(
            cls.session_schema, format_checker=FormatChecker()
        )
        cls.transcript_validator = Draft202012Validator(
            cls.transcript_schema, format_checker=FormatChecker()
        )
        cls.asset_example = json.loads(
            (EXAMPLES / "audio-asset.valid.json").read_text(encoding="utf-8")
        )
        cls.session_example = json.loads(
            (EXAMPLES / "ingestion-session.valid.json").read_text(encoding="utf-8")
        )
        cls.transcript_example = json.loads(
            (EXAMPLES / "transcript.valid.json").read_text(encoding="utf-8")
        )

    def test_schemas_are_valid_draft_2020_12(self) -> None:
        Draft202012Validator.check_schema(self.asset_schema)
        Draft202012Validator.check_schema(self.session_schema)
        Draft202012Validator.check_schema(self.transcript_schema)

    def test_valid_examples_pass(self) -> None:
        self.asset_validator.validate(self.asset_example)
        self.session_validator.validate(self.session_example)
        self.transcript_validator.validate(self.transcript_example)

    def test_stream_session_contract_is_valid(self) -> None:
        session = dict(self.session_example)
        session["mode"] = "stream"
        session["state"] = "receiving"
        session.pop("asset_id")
        self.session_validator.validate(session)

    def test_v1_schema_identity_and_required_fields_are_stable(self) -> None:
        self.assertEqual(self.asset_schema["$id"], "urn:voxpipe:schema:audio-asset:1.0.0")
        self.assertEqual(self.session_schema["$id"], "urn:voxpipe:schema:ingestion-session:1.0.0")
        self.assertEqual(self.transcript_schema["$id"], "urn:voxpipe:schema:transcript:1.0.0")
        self.assertTrue({"transcript_id", "asset_id", "language", "segments", "speakers", "provenance"} <= set(self.transcript_schema["required"]))
        self.assertTrue({"asset_id", "tenant_id", "media", "integrity", "storage"} <= set(self.asset_schema["required"]))
        self.assertTrue({"session_id", "tenant_id", "mode", "state", "idempotency_key"} <= set(self.session_schema["required"]))

    def test_transcript_rejects_unknown_fields_and_invalid_confidence(self) -> None:
        transcript = dict(self.transcript_example)
        transcript["provider_raw_response"] = {"secret": "must-not-be-public"}
        self.assertFalse(self.transcript_validator.is_valid(transcript))
        transcript = dict(self.transcript_example)
        transcript["segments"] = [dict(self.transcript_example["segments"][0], confidence=1.5)]
        self.assertFalse(self.transcript_validator.is_valid(transcript))

    def test_asset_rejects_invalid_checksum(self) -> None:
        asset = dict(self.asset_example)
        asset["integrity"] = {"algorithm": "sha256", "digest": "not-a-sha256"}
        self.assertFalse(self.asset_validator.is_valid(asset))

    def test_asset_rejects_unknown_fields(self) -> None:
        asset = dict(self.asset_example)
        asset["public_download_url"] = "https://example.invalid/audio.wav"
        self.assertFalse(self.asset_validator.is_valid(asset))

    def test_asset_requires_tenant_scope(self) -> None:
        asset = dict(self.asset_example)
        asset.pop("tenant_id")
        self.assertFalse(self.asset_validator.is_valid(asset))

    def test_session_completed_requires_asset_reference(self) -> None:
        session = dict(self.session_example)
        session.pop("asset_id")
        self.assertFalse(self.session_validator.is_valid(session))

    def test_failed_session_requires_failure_details(self) -> None:
        session = dict(self.session_example)
        session["state"] = "failed"
        session.pop("failure", None)
        self.assertFalse(self.session_validator.is_valid(session))

    def test_rejects_invalid_uuid_and_datetime(self) -> None:
        asset = dict(self.asset_example)
        asset["asset_id"] = "not-a-uuid"
        asset["created_at"] = "yesterday"
        self.assertFalse(self.asset_validator.is_valid(asset))


if __name__ == "__main__":
    unittest.main()
