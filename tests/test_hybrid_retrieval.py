import unittest
from uuid import UUID

from voxpipe.retrieval.hybrid import HybridRetrievalService, SearchCandidate, reciprocal_rank_fusion
from voxpipe.retrieval.ports import Embedding, RetrievalFilters


TRANSCRIPT = "00000000-0000-0000-0000-000000000001"
ASSET = "00000000-0000-0000-0000-000000000002"
SEGMENT_A = "00000000-0000-0000-0000-000000000003"
SEGMENT_B = "00000000-0000-0000-0000-000000000004"
SEGMENT_C = "00000000-0000-0000-0000-000000000005"


def candidate(segment_id, start_ms, text):
    return SearchCandidate(TRANSCRIPT, 1, ASSET, segment_id, start_ms, start_ms + 1000, text, "fa-IR")


class HybridRetrievalTests(unittest.TestCase):
    def test_reciprocal_rank_fusion_rewards_cross_retriever_agreement(self):
        a, b, c = candidate(SEGMENT_A, 0, "جلسه بودجه"), candidate(SEGMENT_B, 1000, "تصمیم فروش"), candidate(SEGMENT_C, 2000, "گزارش")
        hits = reciprocal_rank_fusion([a, b], [b, a, c], rank_constant=10)
        self.assertEqual([hit.candidate.segment_id for hit in hits], [SEGMENT_A, SEGMENT_B, SEGMENT_C])
        self.assertEqual(hits[0].lexical_rank, 1)
        self.assertEqual(hits[0].semantic_rank, 2)
        self.assertEqual(hits[1].lexical_rank, 2)
        self.assertEqual(hits[1].semantic_rank, 1)

    def test_fusion_deduplicates_within_each_backend_and_limits_results(self):
        a = candidate(SEGMENT_A, 0, "متن")
        b = candidate(SEGMENT_B, 1000, "متن دوم")
        hits = reciprocal_rank_fusion([a, a, b], [], limit=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].lexical_rank, 1)

    def test_fusion_rejects_inconsistent_provenance_for_same_segment(self):
        a = candidate(SEGMENT_A, 0, "متن")
        altered = candidate(SEGMENT_A, 0, "متن دستکاری‌شده")
        with self.assertRaisesRegex(ValueError, "immutable candidate provenance"):
            reciprocal_rank_fusion([a], [altered])

    def test_fusion_rejects_invalid_weights_and_bounds(self):
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([], [], lexical_weight=0, semantic_weight=0)
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([], [], lexical_weight=float("nan"))
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([], [], limit=101)

    def test_embedding_contract_rejects_dimension_and_non_finite_values(self):
        with self.assertRaises(ValueError):
            Embedding("local", "v1", 2, (1.0,))
        with self.assertRaises(ValueError):
            Embedding("local", "v1", 1, (float("inf"),))

    def test_retrieval_filters_require_tenant_and_validate_source_bounds(self):
        with self.assertRaises(ValueError):
            RetrievalFilters("")
        with self.assertRaises(ValueError):
            RetrievalFilters("tenant", start_ms=100, end_ms=100)
        filters = RetrievalFilters("tenant", transcript_id=TRANSCRIPT, asset_id=ASSET,
            revision=2, language="fa-IR", start_ms=100, end_ms=900)
        self.assertEqual(filters.revision, 2)

    def test_service_propagates_authenticated_tenant_and_all_supported_filters(self):
        a = candidate(SEGMENT_A, 0, "جلسه")
        b = candidate(SEGMENT_B, 1000, "بودجه")
        class Lexical:
            def search_transcript_segments(self, tenant_id, query, **kwargs):
                self.call = (tenant_id, query, kwargs)
                return [a]
        class Embeddings:
            def embed_query(self, text):
                self.text = text
                return Embedding("local", "v1", 2, (0.1, 0.2))
        class Semantic:
            def search_similar(self, tenant_id, embedding, *, filters, limit):
                self.call = (tenant_id, embedding, filters, limit)
                return (b, a)
        lexical, embeddings, semantic = Lexical(), Embeddings(), Semantic()
        filters = RetrievalFilters("authenticated-tenant", transcript_id=TRANSCRIPT,
            asset_id=ASSET, revision=1, language="fa-IR", start_ms=0, end_ms=3000)
        service = HybridRetrievalService(lexical=lexical, embeddings=embeddings, semantic=semantic)
        hits = service.search("جلسه", filters=filters, limit=5)
        self.assertEqual(lexical.call[0], "authenticated-tenant")
        self.assertEqual(lexical.call[2]["transcript_id"], TRANSCRIPT)
        self.assertEqual(semantic.call[0], "authenticated-tenant")
        self.assertIs(semantic.call[2], filters)
        self.assertEqual(embeddings.text, "جلسه")
        self.assertEqual(len(hits), 2)

    def test_service_rejects_missing_authenticated_scope_and_invalid_query(self):
        class Noop:
            def search_transcript_segments(self, *args, **kwargs): return []
            def embed_query(self, text): return Embedding("local", "v1", 1, (0.1,))
            def search_similar(self, *args, **kwargs): return ()
        service = HybridRetrievalService(lexical=Noop(), embeddings=Noop(), semantic=Noop())
        with self.assertRaises(ValueError):
            service.search(" ", filters=RetrievalFilters("tenant"))


    def test_service_normalizes_persian_query_for_both_retrievers(self):
        class Lexical:
            def search_transcript_segments(self, tenant_id, query, **kwargs):
                self.query = query
                return []
        class Embeddings:
            def embed_query(self, text):
                self.query = text
                return Embedding("local", "v1", 1, (1.0,))
        class Semantic:
            def search_similar(self, *args, **kwargs):
                return ()
        lexical, embeddings = Lexical(), Embeddings()
        service = HybridRetrievalService(lexical=lexical, embeddings=embeddings, semantic=Semantic())
        service.search("كِتاب يَك ۱۲۳", filters=RetrievalFilters("authenticated-tenant"))
        self.assertEqual(lexical.query, "کتاب یک 123")
        self.assertEqual(embeddings.query, "کتاب یک 123")

    def test_service_rejects_queries_empty_after_normalization(self):
        class Noop:
            def search_transcript_segments(self, *args, **kwargs): return []
            def embed_query(self, text): return Embedding("local", "v1", 1, (1.0,))
            def search_similar(self, *args, **kwargs): return ()
        service = HybridRetrievalService(lexical=Noop(), embeddings=Noop(), semantic=Noop())
        with self.assertRaisesRegex(ValueError, "after normalization"):
            service.search("\\u0640\\u200c", filters=RetrievalFilters("tenant"))

if __name__ == "__main__":
    unittest.main()
