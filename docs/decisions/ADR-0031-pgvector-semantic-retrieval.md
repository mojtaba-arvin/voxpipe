# ADR-0031: pgvector-backed semantic retrieval

- **Status:** Initial storage/index decision; embedding model and relevance tuning deferred.
- **Date:** 2026-11-22

## Context

Transcript segments are immutable, tenant-scoped PostgreSQL rows with an existing GIN full-text index. Semantic retrieval needs versioned vectors, source provenance, tenant isolation and deletion propagation without introducing a second database.

## Decision

Use PostgreSQL 16 with pgvector 0.8.7 and a 384-dimensional vector column for the initial semantic index. Add an HNSW cosine index and keep vectors keyed by tenant, transcript revision, segment, model and model version. Enforce forced RLS and a composite FK to the source segment. Bind every stored vector to the source segment text SHA-256. Use reciprocal-rank fusion for lexical/vector combination.

## Consequences

Positive: one transactional data plane, FK-driven deletion, RLS, and a mature approximate-nearest-neighbour index. Negative: the 384-dimensional initial schema constrains model choice; HNSW adds write/storage overhead; tenant and model filters may reduce ANN recall and require `ef_search` tuning or partitioning.

## Deferred decisions

No embedding model is selected or downloaded in this change. Select it only after a Persian/multilingual relevance benchmark comparing quality, latency, CPU/RAM, licensing and deployment cost. A human-judged relevance set and HNSW filter-selectivity benchmarks are required before production quality claims.
