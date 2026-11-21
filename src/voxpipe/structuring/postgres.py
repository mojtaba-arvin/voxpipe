"""Append-only tenant-scoped persistence of complete structured extraction payloads."""
from __future__ import annotations
import json
from uuid import UUID, uuid5, NAMESPACE_URL
from dataclasses import asdict
from voxpipe.structuring.contracts import StructuredIntelligence
class StructuredExtractionConflict(RuntimeError): pass
class PostgresStructuredIntelligenceRepository:
    def __init__(self,dsn):
        if not dsn: raise ValueError("PostgreSQL DSN is required")
        self._dsn=dsn
    def create_or_get(self,tenant_id,idempotency_key,result:StructuredIntelligence):
        tenant=str(UUID(tenant_id))
        if not idempotency_key or len(idempotency_key)>256: raise ValueError("invalid idempotency key")
        payload=asdict(result); payload["provenance"]["extracted_at"]=result.provenance.extracted_at.isoformat()
        encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True)
        normalized=json.loads(encoded)
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('voxpipe.tenant_id',%s,true)",(tenant,))
            row=conn.execute("""INSERT INTO structured_extractions
              (tenant_id,extraction_id,document_id,asset_id,transcript_id,transcript_revision,idempotency_key,result)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
              ON CONFLICT (tenant_id,idempotency_key) DO NOTHING RETURNING extraction_id,result""",
              (tenant,str(uuid5(NAMESPACE_URL,tenant+":"+idempotency_key)),payload["document_id"],payload["asset_id"],
               payload["transcript_id"],payload["transcript_revision"],idempotency_key,encoded)).fetchone()
            if row is None:
                row=conn.execute("SELECT extraction_id,result FROM structured_extractions WHERE tenant_id=%s AND idempotency_key=%s",(tenant,idempotency_key)).fetchone()
                if row is None or row["result"]!=normalized: raise StructuredExtractionConflict("idempotency key reused for different result")
            return {"extraction_id":str(row["extraction_id"]),"result":row["result"]}
    def get(self,tenant_id,extraction_id):
        tenant,extraction=str(UUID(tenant_id)),str(UUID(extraction_id))
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('voxpipe.tenant_id',%s,true)",(tenant,))
            row=conn.execute("SELECT extraction_id,result FROM structured_extractions WHERE tenant_id=%s AND extraction_id=%s",(tenant,extraction)).fetchone()
        return {"extraction_id":str(row["extraction_id"]),"result":row["result"]} if row else None
