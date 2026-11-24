# Hybrid transcript retrieval

## Current implementation

VoxPipe retains PostgreSQL full-text search as its lexical source and now has a pgvector-backed semantic adapter. A provider-neutral embedding port feeds 384-dimensional vectors into a tenant-scoped `transcript_segment_embeddings` table. The table uses an HNSW cosine index, immutable transcript-segment foreign keys, a source-text SHA-256 binding, model/version keys and forced RLS. Deleting the source asset cascades through transcript revisions, segments and embeddings.

`PostgresSemanticSearchAdapter` stores vectors only when the supplied candidate exactly matches the immutable tenant-scoped transcript segment. Queries must use the same embedding model/version as the index and can filter by tenant, transcript, asset, revision, language, speaker and time. Obsolete model versions can be removed within tenant scope. Entity filtering deliberately fails closed until an extraction entity index exists.

`HybridRetrievalService` combines lexical and semantic ranked lists using deterministic weighted reciprocal-rank fusion. Backend score scales are not compared directly. Duplicate segment identities count once, and conflicting source provenance between backends is rejected.

## Persian query normalization

All lexical and semantic searches now receive the same deterministic `fa-query-v1` normalization: Arabic/Persian Yeh and Kaf variants, common Alef variants and digits are folded; diacritics/tatweel are removed; ZWNJ becomes a token boundary; whitespace is collapsed and mixed Latin text is case-folded. Quotes, punctuation and PostgreSQL web-search operators are preserved. This avoids lexical/vector disagreement caused only by common Persian orthographic variants; it is not stemming or a substitute for a human-judged relevance benchmark.

## Evaluation

`evaluate_retrieval` reports macro Recall@K, MRR and binary nDCG for a versioned labelled query set. The evaluator has unit tests, but this repository still lacks a human-judged Persian relevance set; no retrieval-quality score is claimed. Index latency, storage cost, recall under multi-tenant filter selectivity and embedding model quality remain to be benchmarked.

## Model and operational boundary

The database schema fixes the initial vector dimension at 384 to keep the index homogeneous. `LocalSentenceTransformerEmbeddingAdapter` can generate query and document vectors from a pre-provisioned local SentenceTransformer directory, uses explicit model/version metadata, supports model-specific query/document prefixes, verifies the configured dimension and normalized finite output, and never downloads weights at runtime. The optional `sentence-transformers` dependency is imported only when the adapter is constructed without an injected encoder. No production model is selected by default; model provisioning, multilingual Persian quality/latency benchmarking and a versioned human-judged relevance set remain required. Entity filters are unsupported until the extraction index is designed and tested.
