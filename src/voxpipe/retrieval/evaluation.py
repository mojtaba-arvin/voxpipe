"""Relevance metrics for versioned retrieval benchmarks."""
from __future__ import annotations
import math
from collections.abc import Mapping, Sequence


def evaluate_retrieval(relevance: Mapping[str, set[str]], ranked_results: Mapping[str, Sequence[str]], *,
                       ks: tuple[int, ...] = (1, 5, 10)) -> dict:
    """Compute macro Recall@K, MRR and binary nDCG over a labelled query set."""
    if not relevance:
        raise ValueError("relevance set must contain at least one query")
    if not isinstance(ks, tuple) or not ks or any(not isinstance(k, int) or isinstance(k, bool) or k < 1 for k in ks):
        raise ValueError("ks must be a non-empty tuple of positive integers")
    if len(set(ks)) != len(ks):
        raise ValueError("ks values must be unique")
    recalls = {k: [] for k in ks}
    reciprocal_ranks, ndcgs = [], []
    for query_id, relevant in relevance.items():
        if not isinstance(query_id, str) or not query_id or not isinstance(relevant, set) or not relevant:
            raise ValueError("each query must have a non-empty set of relevant IDs")
        ranked = ranked_results.get(query_id, ())
        if any(not isinstance(item, str) or not item for item in ranked) or len(set(ranked)) != len(ranked):
            raise ValueError("ranked result IDs must be non-empty and unique per query")
        reciprocal_rank = next((1.0 / rank for rank, item in enumerate(ranked, 1) if item in relevant), 0.0)
        reciprocal_ranks.append(reciprocal_rank)
        for k in ks:
            recalls[k].append(len(set(ranked[:k]) & relevant) / len(relevant))
        dcg = sum(1.0 / math.log2(rank + 1) for rank, item in enumerate(ranked, 1) if item in relevant)
        ideal_count = min(len(relevant), len(ranked))
        ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        ndcgs.append(dcg / ideal_dcg if ideal_dcg else 0.0)
    return {
        "recall_at_k": {str(k): sum(values) / len(values) for k, values in recalls.items()},
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
        "ndcg": sum(ndcgs) / len(ndcgs),
        "query_count": len(relevance),
    }
