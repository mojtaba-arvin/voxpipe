"""Local-only sentence-transformer embedding adapter.

Model files are provisioned out of band; runtime construction never downloads weights.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from voxpipe.retrieval.ports import Embedding


class LocalSentenceTransformerEmbeddingAdapter:
    """Create versioned query/document embeddings from a pre-provisioned local model."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        model: str,
        model_version: str,
        dimensions: int = 384,
        device: str = "cpu",
        query_prefix: str = "",
        document_prefix: str = "",
        encoder: Any | None = None,
    ) -> None:
        if not isinstance(model_path, (str, Path)) or not str(model_path).strip():
            raise ValueError("model_path must be a non-empty local path")
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise ValueError("model must be a non-empty bounded identifier")
        if not isinstance(model_version, str) or not model_version.strip() or len(model_version) > 128:
            raise ValueError("model_version must be a non-empty bounded revision")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1:
            raise ValueError("dimensions must be a positive integer")
        if not isinstance(device, str) or device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda")
        for name, prefix in (("query_prefix", query_prefix), ("document_prefix", document_prefix)):
            if not isinstance(prefix, str) or len(prefix) > 128:
                raise ValueError(f"{name} must be a bounded string")

        self.model = model
        self.model_version = model_version
        self.dimensions = dimensions
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix

        if encoder is None:
            path = Path(model_path)
            if not path.is_dir():
                raise FileNotFoundError("embedding model must be pre-provisioned as a local directory")
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "install the optional sentence-transformers runtime to use local embeddings"
                ) from exc
            encoder = SentenceTransformer(
                str(path), device=device, local_files_only=True
            )
        reported_dimensions = encoder.get_sentence_embedding_dimension()
        if reported_dimensions != dimensions:
            raise ValueError(
                f"local embedding model dimension {reported_dimensions!r} does not match configured {dimensions}"
            )
        self._encoder = encoder

    def _embed_batch(self, texts: list[str] | tuple[str, ...], prefix: str) -> tuple[Embedding, ...]:
        if not isinstance(texts, (list, tuple)) or not 1 <= len(texts) <= 128:
            raise ValueError("embedding batch must contain between 1 and 128 texts")
        for text in texts:
            if not isinstance(text, str) or not text.strip() or len(text) > 16_384:
                raise ValueError("embedding text must contain 1..16384 characters")
        values = self._encoder.encode(
            [prefix + text for text in texts],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if hasattr(values, "tolist"):
            values = values.tolist()
        if not isinstance(values, (list, tuple)) or len(values) != len(texts):
            raise ValueError("local embedding model returned an invalid batch shape")
        embeddings = []
        for vector in values:
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            if not isinstance(vector, (list, tuple)) or len(vector) != self.dimensions:
                raise ValueError("local embedding model returned an unexpected vector dimension")
            normalized = tuple(float(value) for value in vector)
            if any(not math.isfinite(value) for value in normalized):
                raise ValueError("local embedding model returned non-finite values")
            norm = math.sqrt(sum(value * value for value in normalized))
            if not math.isfinite(norm) or not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
                raise ValueError("local embedding model did not return a normalized vector")
            embeddings.append(Embedding(self.model, self.model_version, self.dimensions, normalized))
        return tuple(embeddings)

    def _embed(self, text: str, prefix: str) -> Embedding:
        return self._embed_batch([text], prefix)[0]

    def embed_documents(self, texts: list[str] | tuple[str, ...]) -> tuple[Embedding, ...]:
        """Embed a bounded batch of transcript segments using the document prefix."""
        return self._embed_batch(texts, self.document_prefix)

    def embed_query(self, text: str) -> Embedding:
        """Embed a search query using the configured model-specific query prefix."""
        return self._embed(text, self.query_prefix)

    def embed_document(self, text: str) -> Embedding:
        """Embed a transcript segment using the configured model-specific document prefix."""
        return self._embed(text, self.document_prefix)
