"""Tenant-scoped lexical and semantic retrieval primitives."""
from voxpipe.retrieval.hybrid import HybridRetrievalService, SearchCandidate, SearchHit, reciprocal_rank_fusion
from voxpipe.retrieval.ports import Embedding, EmbeddingPort, RetrievalFilters, SemanticSearchPort
from voxpipe.retrieval.postgres import PostgresSemanticSearchAdapter
from voxpipe.retrieval.evaluation import evaluate_retrieval
from voxpipe.retrieval.local_embeddings import LocalSentenceTransformerEmbeddingAdapter

__all__ = [
    "Embedding", "EmbeddingPort", "LocalSentenceTransformerEmbeddingAdapter", "HybridRetrievalService", "PostgresSemanticSearchAdapter",
    "RetrievalFilters", "SearchCandidate", "SearchHit", "SemanticSearchPort",
    "evaluate_retrieval", "reciprocal_rank_fusion",
]
