# Hybrid transcript retrieval

## Current implementation

VoxPipe retains PostgreSQL full-text search as its lexical source and now has a pgvector-backed semantic adapter. A provider-neutral embedding port feeds 384-dimensional vectors into a tenant-scoped `transcript_segment_embeddings` table. The table uses an HNSW cosine index, immutable transcript-segment foreign keys, a source-text SHA-256 binding, model/version keys and forced RLS. Deleting the source asset cascades through transcript revisions, segments and embeddings.

`PostgresSemanticSearchAdapter` stores vectors only when the supplied candidate exactly matches the immutable tenant-scoped transcript segment. Queries must use the same embedding model/version as the index and can filter by tenant, transcript, asset, revision, language, speaker and time. Obsolete model versions can be removed within tenant scope. Entity filtering deliberately fails closed until an extraction entity index exists.

`HybridRetrievalService` combines lexical and semantic ranked lists using deterministic weighted reciprocal-rank fusion. Backend score scales are not compared directly. Duplicate segment identities count once, and conflicting source provenance between backends is rejected.

## Evaluation

`evaluate_retrieval` reports macro Recall@K, MRR and binary nDCG for a versioned labelled query set. The evaluator has unit tests, but this repository still lacks a human-judged Persian relevance set; no retrieval-quality score is claimed. Index latency, storage cost, recall under multi-tenant filter selectivity and embedding model quality remain to be benchmarked.

## Model and operational boundary

The database schema fixes the initial vector dimension at 384 to keep the index homogeneous. It does not select or download an embedding model. The model/version is supplied by the embedding provider and must be recorded consistently. The current adapter does not generate embeddings itself. Entity filters are unsupported until the extraction index is designed and tested.
