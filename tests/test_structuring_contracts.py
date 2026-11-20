"""Evidence, provenance, and source-binding tests for structured intelligence."""

import unittest
from datetime import datetime, timezone
from dataclasses import replace

from voxpipe.structuring import (
    EvidenceSpan, ExtractionProvenance, StructuredClaim, StructuredIntelligence,
    StructuredIntelligenceValidationError, validate_structured_intelligence,
)

ASSET = "9d6eaa44-70c4-4d42-91d8-47d4d4c6f002"
TRANSCRIPT = "8e45d7a1-9b7b-4c43-a2b4-4d88efc4c101"
SEGMENT = "a0f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0101"
CLAIM = "b0f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0101"
DOCUMENT = "c0f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0101"
TEXT = "فردا جلسه ساعت ده برگزار می‌شود."


def valid_result():
    return StructuredIntelligence(
        document_id=DOCUMENT, asset_id=ASSET, transcript_id=TRANSCRIPT, transcript_revision=3,
        provenance=ExtractionProvenance("local", "fixture", "1", "prompt-v1", "1.0.0", datetime.now(timezone.utc)),
        events=(StructuredClaim(CLAIM, {"type": "meeting"}, (EvidenceSpan(SEGMENT, 0, 9, TEXT[:9]),), 0.95),),
    )


class StructuredIntelligenceTests(unittest.TestCase):
    def validate(self, result):
        validate_structured_intelligence(result, expected_asset_id=ASSET, expected_transcript_id=TRANSCRIPT,
            expected_revision=3, transcript_segments={SEGMENT: TEXT})

    def test_exact_evidence_and_provenance_pass(self):
        self.validate(valid_result())

    def test_rejects_mismatched_asset_transcript_or_revision(self):
        result = valid_result()
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, asset_id=TRANSCRIPT))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, transcript_revision=2))

    def test_rejects_fabricated_quote_and_out_of_range_offsets(self):
        result = valid_result()
        claim = result.events[0]
        bad_quote = replace(claim, evidence=(replace(claim.evidence[0], quote="متن ساختگی"),))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, events=(bad_quote,)))
        bad_range = replace(claim, evidence=(replace(claim.evidence[0], end_char=len(TEXT) + 1),))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, events=(bad_range,)))

    def test_rejects_unknown_segment_and_missing_evidence(self):
        result = valid_result()
        claim = result.events[0]
        unknown = replace(claim, evidence=(replace(claim.evidence[0], segment_id="d0f7d9b8-0f7b-4b16-a2b2-2c0d9c8a0101"),))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, events=(unknown,)))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, events=(replace(claim, evidence=()),)))

    def test_rejects_duplicate_claim_ids_and_invalid_confidence(self):
        result = valid_result()
        duplicate = replace(result.events[0], confidence=0.5)
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, entities=(duplicate,)))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, events=(replace(result.events[0], confidence=float("nan")),)))

    def test_rejects_naive_extraction_timestamp(self):
        result = valid_result()
        provenance = replace(result.provenance, extracted_at=datetime(2026, 1, 1))
        with self.assertRaises(StructuredIntelligenceValidationError):
            self.validate(replace(result, provenance=provenance))


if __name__ == "__main__":
    unittest.main()
