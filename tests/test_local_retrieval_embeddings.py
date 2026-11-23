import unittest

from voxpipe.retrieval import LocalSentenceTransformerEmbeddingAdapter


class FakeEncoder:
    def __init__(self, dimension=2, values=(0.6, 0.8)):
        self.dimension = dimension
        self.values = values
        self.calls = []

    def get_sentence_embedding_dimension(self):
        return self.dimension

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return [list(self.values)]


class LocalEmbeddingAdapterTests(unittest.TestCase):
    def make_adapter(self, encoder=None, **kwargs):
        return LocalSentenceTransformerEmbeddingAdapter(
            "/models/local-e5", model="local-e5", model_version="sha256:abc",
            dimensions=2, encoder=encoder or FakeEncoder(), **kwargs,
        )

    def test_query_and_document_embeddings_use_distinct_configured_prefixes(self):
        encoder = FakeEncoder()
        adapter = self.make_adapter(
            encoder, query_prefix="query: ", document_prefix="passage: "
        )
        query = adapter.embed_query("جلسه بودجه")
        document = adapter.embed_document("جلسه بودجه")
        self.assertEqual(encoder.calls[0][0], ["query: جلسه بودجه"])
        self.assertEqual(encoder.calls[1][0], ["passage: جلسه بودجه"])
        self.assertTrue(encoder.calls[0][1]["normalize_embeddings"])
        self.assertEqual(query.model, "local-e5")
        self.assertEqual(query.model_version, "sha256:abc")
        self.assertEqual(query.values, document.values)

    def test_rejects_model_dimension_mismatch(self):
        with self.assertRaisesRegex(ValueError, "dimension"):
            self.make_adapter(encoder=FakeEncoder(dimension=3))

    def test_rejects_invalid_or_non_normalized_model_output(self):
        for values in ((0.0, 0.0), (float("nan"), 1.0), (0.2, 0.2)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.make_adapter(encoder=FakeEncoder(values=values)).embed_query("متن")

    def test_rejects_wrong_batch_shape_and_empty_text(self):
        class BadBatch(FakeEncoder):
            def encode(self, texts, **kwargs):
                return [0.6, 0.8]
        adapter = self.make_adapter(encoder=BadBatch())
        with self.assertRaisesRegex(ValueError, "batch shape"):
            adapter.embed_query("متن")
        with self.assertRaisesRegex(ValueError, "1..16384"):
            adapter.embed_query(" ")

    def test_runtime_requires_preprovisioned_local_model_directory(self):
        with self.assertRaisesRegex(FileNotFoundError, "pre-provisioned"):
            LocalSentenceTransformerEmbeddingAdapter(
                "/path/that/does/not/exist", model="local", model_version="v1",
                dimensions=2,
            )


if __name__ == "__main__":
    unittest.main()
