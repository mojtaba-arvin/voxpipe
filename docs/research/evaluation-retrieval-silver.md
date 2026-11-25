# Retrieval silver evaluation

## Query generation

Run `python -m eval.issue48.queries --count 120 --seed 20261125`. The generator reads the validated corpus through `eval._shared.corpus_loader`, so `CORPUS_PATH` overrides the committed five-passage synthetic placeholder. It creates 100–200 deterministic Persian query templates with stable `query_id`, `source_passage_id`, anchor terms, seed and a 70/15/15 train/dev/test split. Every query is anchored to a passage that contains its answer.

## Heuristic qrels

Run `python -m eval.issue48.qrels`. The source passage receives grade 2. Other passages receive grade 1 when token-set Jaccard overlap is at least 0.5; all remaining passages receive grade 0. The threshold is fixed and recorded in the qrels artifact. Qrels are sorted by `query_id`, then `passage_id`; equal-overlap ranking ties are resolved by `passage_id` ascending.

These are heuristic silver judgments, not human relevance assessments. Grade 1 and grade 2 are both treated as binary relevant by the existing evaluator for Recall@5, MRR and nDCG@5.

## Ranking and metrics

Run `python -m eval.issue48.lexical_baseline` to produce a deterministic Jaccard lexical-only run. It is deliberately not the production hybrid/semantic retrieval service. The project has no selected production embedding model, and the optional hybrid service remains disabled unless a model is provisioned out of band; this evaluation does not bypass that decision.

Run `python -m eval.issue48.metrics`. It reuses `voxpipe.retrieval.evaluation.evaluate_retrieval`, evaluates the held-out test split only, and writes `metrics_48.json`. The formulas are:

- Recall@5: number of binary-relevant passages in the first five results divided by the number of relevant passages for that query, macro-averaged across test queries.
- MRR: reciprocal rank of the first relevant result, averaged across test queries; no hit contributes zero.
- Binary nDCG@5: DCG/IDCG over the first five results, with gain 1 for any qrels grade >= 1 and discount `log2(rank + 1)`; no relevant result contributes zero.

The metrics artifact records the seed, split counts/ratios, qrels threshold, ranking method and interpretation warnings. The five-passage placeholder makes Recall@5 saturated by construction; it validates pipeline wiring only. A larger injected corpus is required for meaningful Recall@5 discrimination.

## Interpretation boundary

This path validates deterministic query generation, qrels, ranking and metric plumbing. It does not measure semantic/hybrid model quality, human relevance, production Persian search, or natural-conversation coverage. Do not close issue #48 until human-judged relevance evidence exists.
