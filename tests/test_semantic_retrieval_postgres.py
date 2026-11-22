"""pgvector integration tests against PostgreSQL 16 with the vector extension."""
from __future__ import annotations
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from voxpipe.ingestion import apply_migrations
from voxpipe.retrieval.hybrid import SearchCandidate
from voxpipe.retrieval.ports import Embedding, RetrievalFilters
from voxpipe.retrieval.postgres import PostgresSemanticSearchAdapter

ADMIN_DSN = os.environ.get("VOXPIPE_TEST_POSTGRES_ADMIN_DSN")
APP_DSN = "postgresql://voxpipe_app:voxpipe_app@localhost:5432/voxpipe_test"
MODEL = "test-multilingual-embedding"
MODEL_VERSION = "test-v1"


@unittest.skipUnless(ADMIN_DSN, "disposable PostgreSQL integration DSN is not configured")
class PgvectorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_migrations(ADMIN_DSN)
        with psycopg.connect(ADMIN_DSN) as connection:
            connection.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='voxpipe_app') THEN CREATE ROLE voxpipe_app LOGIN PASSWORD 'voxpipe_app'; END IF; END $$"
            )
            connection.execute("GRANT SELECT ON transcripts, transcript_segments TO voxpipe_app")
            connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON transcript_segment_embeddings TO voxpipe_app")

    def setUp(self):
        self.tenant = str(uuid4())
        self.asset = str(uuid4())
        self.transcript = str(uuid4())
        self.segment_a = str(uuid4())
        self.segment_b = str(uuid4())
        self.adapter = PostgresSemanticSearchAdapter(APP_DSN, model=MODEL, model_version=MODEL_VERSION)
        self.text_a = "جلسه بودجه و تصمیم‌های مالی"
        self.text_b = "گزارش فروش و برنامه بازاریابی"
        self.candidate_a = SearchCandidate(self.transcript, 1, self.asset, self.segment_a,
            0, 1500, self.text_a, "fa-IR")
        self.candidate_b = SearchCandidate(self.transcript, 1, self.asset, self.segment_b,
            1500, 3000, self.text_b, "fa-IR")
        with psycopg.connect(ADMIN_DSN) as connection:
            connection.execute(
                "INSERT INTO audio_assets(asset_id,tenant_id,payload) VALUES (%s,%s,%s)",
                (self.asset, self.tenant, Jsonb({"source": "integration-test"})),
            )
            connection.execute(
                """INSERT INTO transcripts(transcript_id,tenant_id,asset_id,revision,language,text,
                       provider,model,model_version,audio_sha256,generated_at)
                   VALUES (%s,%s,%s,1,'fa-IR',%s,'test','test','test-v1',%s,%s)""",
                (self.transcript, self.tenant, self.asset, self.text_a + " " + self.text_b,
                 "a" * 64, datetime.now(timezone.utc)),
            )
            connection.execute(
                """INSERT INTO transcript_segments(tenant_id,transcript_id,revision,segment_id,
                       start_ms,end_ms,text,confidence,speaker_id)
                   VALUES (%s,%s,1,%s,0,1500,%s,0.9,NULL),(%s,%s,1,%s,1500,3000,%s,0.9,NULL)""",
                (self.tenant, self.transcript, self.segment_a, self.text_a,
                 self.tenant, self.transcript, self.segment_b, self.text_b),
            )

    def vector(self, first, second):
        values = [0.0] * 384
        values[0], values[1] = first, second
        return Embedding(MODEL, MODEL_VERSION, 384, tuple(values))

    def test_store_and_search_are_tenant_and_source_filtered(self):
        self.adapter.store_embedding(self.tenant, self.candidate_a, self.vector(1, 0))
        self.adapter.store_embedding(self.tenant, self.candidate_b, self.vector(0, 1))
        results = self.adapter.search_similar(self.tenant, self.vector(.99, .01),
            filters=RetrievalFilters(self.tenant, language="fa-IR"), limit=10)
        self.assertEqual([item.segment_id for item in results], [self.segment_a, self.segment_b])
        other_tenant = str(uuid4())
        self.assertEqual(self.adapter.search_similar(other_tenant, self.vector(.99, .01),
            filters=RetrievalFilters(other_tenant), limit=10), ())

    def test_store_rejects_stale_source_text_and_wrong_embedding_version(self):
        self.adapter.store_embedding(self.tenant, self.candidate_a, self.vector(1, 0))
        altered = SearchCandidate(self.transcript, 1, self.asset, self.segment_a,
            0, 1500, "متن تغییر کرده", "fa-IR")
        with self.assertRaises(ValueError):
            self.adapter.store_embedding(self.tenant, altered, self.vector(1, 0))
        with self.assertRaises(ValueError):
            self.adapter.store_embedding(self.tenant, self.candidate_a,
                Embedding(MODEL, "wrong-version", 384, tuple([0.0] * 384)))

    def test_zero_vectors_are_rejected_and_tenant_uuid_is_canonicalized(self):
        with self.assertRaises(ValueError):
            self.adapter.search_similar(self.tenant, self.vector(0, 0),
                filters=RetrievalFilters(self.tenant), limit=5)
        self.assertEqual(self.adapter.search_similar(self.tenant.upper(), self.vector(1, 0),
            filters=RetrievalFilters(self.tenant.upper()), limit=5), ())

    def test_entity_filter_fails_closed_until_entity_index_exists(self):
        with self.assertRaises(ValueError):
            self.adapter.search_similar(self.tenant, self.vector(1, 0),
                filters=RetrievalFilters(self.tenant, entity_ids=(str(uuid4()),)), limit=5)

    def test_model_version_cleanup_and_asset_deletion_cascade(self):
        self.adapter.store_embedding(self.tenant, self.candidate_a, self.vector(1, 0))
        self.assertEqual(self.adapter.delete_model_version(self.tenant, model=MODEL, model_version=MODEL_VERSION), 1)
        self.adapter.store_embedding(self.tenant, self.candidate_a, self.vector(1, 0))
        with psycopg.connect(ADMIN_DSN) as connection:
            connection.execute("DELETE FROM audio_assets WHERE tenant_id=%s AND asset_id=%s",
                (self.tenant, self.asset))
            count = connection.execute(
                "SELECT count(*) FROM transcript_segment_embeddings WHERE tenant_id=%s AND transcript_id=%s",
                (self.tenant, self.transcript),
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
