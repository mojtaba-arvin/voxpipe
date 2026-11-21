"""Append-only tenant-scoped persistence of validated structured extraction payloads."""
from __future__ import annotations

import json
from dataclasses import asdict
from uuid import NAMESPACE_URL, UUID, uuid5

from voxpipe.structuring.contracts import (
    StructuredIntelligence,
    validate_structured_intelligence,
)


class StructuredExtractionConflict(RuntimeError):
    """An idempotency key was reused for different extraction content."""


class StructuredExtractionSourceNotFound(RuntimeError):
    """The tenant cannot read the referenced immutable transcript revision."""


class PostgresStructuredIntelligenceRepository:
    """Persist validated claims, re-checking evidence inside the tenant DB boundary."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @staticmethod
    def _payload(result: StructuredIntelligence) -> tuple[dict, str, dict]:
        if not isinstance(result, StructuredIntelligence):
            raise TypeError("result must be StructuredIntelligence")
        payload = asdict(result)
        payload["provenance"]["extracted_at"] = result.provenance.extracted_at.isoformat()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return payload, encoded, json.loads(encoded)

    def create_or_get(
        self, tenant_id: str, idempotency_key: str, result: StructuredIntelligence
    ) -> dict:
        tenant = str(UUID(tenant_id))
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 256:
            raise ValueError("idempotency_key must be a non-empty bounded string")
        payload, encoded, normalized = self._payload(result)

        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant,)
            )
            source = connection.execute(
                """SELECT asset_id FROM transcripts
                   WHERE tenant_id=%s AND transcript_id=%s AND revision=%s""",
                (tenant, payload["transcript_id"], payload["transcript_revision"]),
            ).fetchone()
            if source is None:
                raise StructuredExtractionSourceNotFound(
                    "source transcript revision is unavailable in this tenant"
                )
            if str(source["asset_id"]) != payload["asset_id"]:
                raise ValueError("extraction asset does not match source transcript revision")
            segment_rows = connection.execute(
                """SELECT segment_id, text FROM transcript_segments
                   WHERE tenant_id=%s AND transcript_id=%s AND revision=%s""",
                (tenant, payload["transcript_id"], payload["transcript_revision"]),
            ).fetchall()
            segments = {str(row["segment_id"]): row["text"] for row in segment_rows}
            validate_structured_intelligence(
                result,
                expected_asset_id=str(source["asset_id"]),
                expected_transcript_id=payload["transcript_id"],
                expected_revision=payload["transcript_revision"],
                transcript_segments=segments,
            )

            row = connection.execute(
                """INSERT INTO structured_extractions
                   (tenant_id, extraction_id, document_id, asset_id, transcript_id,
                    transcript_revision, idempotency_key, result)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (tenant_id,idempotency_key) DO NOTHING
                   RETURNING extraction_id,result""",
                (
                    tenant,
                    str(uuid5(NAMESPACE_URL, tenant + ":" + idempotency_key)),
                    payload["document_id"],
                    payload["asset_id"],
                    payload["transcript_id"],
                    payload["transcript_revision"],
                    idempotency_key,
                    encoded,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT extraction_id,result FROM structured_extractions
                       WHERE tenant_id=%s AND idempotency_key=%s""",
                    (tenant, idempotency_key),
                ).fetchone()
                if row is None or row["result"] != normalized:
                    raise StructuredExtractionConflict(
                        "idempotency key already stores different extraction content"
                    )
            return {"extraction_id": str(row["extraction_id"]), "result": row["result"]}

    def get(self, tenant_id: str, extraction_id: str) -> dict | None:
        tenant, extraction = str(UUID(tenant_id)), str(UUID(extraction_id))
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('voxpipe.tenant_id', %s, true)", (tenant,)
            )
            row = connection.execute(
                """SELECT extraction_id,result FROM structured_extractions
                   WHERE tenant_id=%s AND extraction_id=%s""",
                (tenant, extraction),
            ).fetchone()
        return (
            {"extraction_id": str(row["extraction_id"]), "result": row["result"]}
            if row else None
        )
