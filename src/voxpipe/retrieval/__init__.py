"""Tenant-scoped lexical and semantic retrieval primitives."""
from voxpipe.retrieval.hybrid import HybridRetrievalService, SearchCandidate, SearchHit, reciprocal_rank_fusion
from voxpipe.retrieval.ports import Embedding, EmbeddingPort, RetrievalFilters, SemanticSearchPort
from voxpipe.retrieval.postgres import PostgresSemanticSearchAdapter
from voxpipe.retrieval.evaluation import evaluate_retrieval

__all__ = [
    "Embedding", "EmbeddingPort", "HybridRetrievalService", "PostgresSemanticSearchAdapter",
    "RetrievalFilters", "SearchCandidate", "SearchHit", "SemanticSearchPort",
    "evaluate_retrieval", "reciprocal_rank_fusion",
]
