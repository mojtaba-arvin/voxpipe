# Hybrid transcript retrieval

## Current boundary

VoxPipe keeps PostgreSQL full-text search as the lexical source of truth and defines provider-neutral embedding and semantic-search ports under `voxpipe.retrieval`. `HybridRetrievalService` obtains both ranked lists under the authenticated tenant scope and fuses them with weighted reciprocal-rank fusion (RRF). Backend score scales are deliberately not compared directly.

## Contracts and security

- `RetrievalFilters.tenant_id` must come from authenticated request context; it is passed unchanged to both backends.
- The same source, time, speaker and language filters are passed to lexical and semantic retrieval. Entity IDs are part of the semantic filter contract for the structured-entity index.
- Every result carries transcript ID, immutable revision, asset ID, segment ID, timestamps, language and optional anonymous speaker ID.
- If the same segment identity appears in both result sets with conflicting source metadata or text, fusion fails closed.
- Embedding model and version are explicit. Embeddings must have a declared dimension and finite values.
- The semantic backend is responsible for enforcing PostgreSQL RLS and all supplied filters; the protocol does not itself provide tenant isolation.

## Ranking

For a candidate at rank (r), weighted RRF contributes (w/(k+r)) from each retrieval list. Default (k=60) and equal weights are conservative starting values, not tuned relevance claims. Ties are resolved deterministically by transcript, revision, start time and segment ID. Duplicate identities within one backend count only at their first rank.

## Not yet implemented

This change does not add pgvector, an embedding model, vector persistence, background re-embedding, or a relevance benchmark. Those require a measured choice of embedding model/dimension, PostgreSQL extension availability in CI/production, tenant-RLS migration tests, and a versioned query/relevance set. No customer transcript is used as public benchmark data.
