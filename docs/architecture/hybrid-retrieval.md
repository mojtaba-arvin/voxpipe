# Hybrid transcript retrieval

## Current implementation

VoxPipe retains PostgreSQL full-text search as its lexical source and now has a pgvector-backed semantic adapter. A provider-neutral embedding port feeds 384-dimensional vectors into a tenant-scoped `transcript_segment_embeddings` table. The table uses an HNSW cosine index, immutable transcript-segment foreign keys, a source-text SHA-256 binding, model/version keys and forced RLS. Deleting the source asset cascades through transcript revisions, segments and embeddings.

`PostgresSemanticSearchAdapter` stores vectors only when the supplied candidate exactly matches the immutable tenant-scoped transcript segment. Queries must use the same embedding model/version as the index and can filter by tenant, transcript, asset, revision, language, speaker and time. Obsolete model versions can be removed within tenant scope. Entity filtering deliberately fails closed until an extraction entity index exists.

`HybridRetrievalService` combines lexical and semantic ranked lists using deterministic weighted reciprocal-rank fusion. Backend score scales are not compared directly. Duplicate segment identities count once, and conflicting source provenance between backends is rejected.

## Persian query normalization

All lexical and semantic searches now receive the same deterministic `fa-query-v1` normalization: Arabic/Persian Yeh and Kaf variants, common Alef variants and digits are folded; diacritics/tatweel are removed; ZWNJ becomes a token boundary; whitespace is collapsed and mixed Latin text is case-folded. Quotes, punctuation and PostgreSQL web-search operators are preserved. This avoids lexical/vector disagreement caused only by common Persian orthographic variants; it is not stemming or a substitute for a human-judged relevance benchmark.

## Evaluation

`evaluate_retrieval` reports macro Recall@K, MRR and binary nDCG for a versioned labelled query set. The evaluator has deterministic unit/integration tests, but this repository still lacks a human-judged Persian relevance set; no retrieval-quality score is claimed. A human-judged set, production embedding selection, index latency, storage cost and recall under multi-tenant filter selectivity are non-gating product-quality follow-ups.

## Model and operational boundary

The database schema fixes the initial vector dimension at 384 to keep the index homogeneous. `LocalSentenceTransformerEmbeddingAdapter` can generate query and document vectors from a pre-provisioned local SentenceTransformer directory, uses explicit model/version metadata, supports model-specific query/document prefixes, verifies the configured dimension and normalized finite output, and never downloads weights at runtime. The optional `sentence-transformers` dependency is imported only when the adapter is constructed without an injected encoder. No production model is selected by default; model provisioning, multilingual Persian quality/latency benchmarking and a versioned human-judged relevance set remain optional product-quality follow-ups and are not repository milestone acceptance gates. Entity filters are unsupported until the extraction index is designed and tested.

## Authenticated API and MCP exposure

The Go API now exposes two versioned routes:

- `POST /v1/transcripts/{transcript_id}/index?revision=N` requires the dedicated `transcript:index` scope. It reads an immutable transcript revision under the authenticated tenant and upserts segment vectors bound to source-text SHA-256, model and model version. Repeating the same request is safe.
- `POST /v1/retrieval/hybrid-search` requires `transcript:read`. Tenant identity is derived from the bearer principal; the request schema does not accept a tenant ID. Results include source provenance and fused/lexical/semantic ranks, but omit tenant IDs, audio hashes and storage references.

The matching MCP tools are `index_transcript` and `search_hybrid_transcripts`. The former is marked non-read-only and requires `transcript:index`; the latter is read-only and requires `transcript:read`.

## Local model service boundary

Embedding inference runs in a private Python sidecar, not in the public Go API process. The Go API forwards only the authenticated tenant ID and validated request over the internal Compose network using a separate shared secret. The sidecar is not host-published and accepts no public bearer tokens.

Enable the optional Compose `retrieval` profile only after provisioning a SentenceTransformer model directory out of band. Configure these values in the local `.env`:

- `VOXPIPE_RETRIEVAL_URL=http://retrieval:8090`
- `VOXPIPE_RETRIEVAL_INTERNAL_TOKEN`: random secret with at least 32 characters, shared only by the API and sidecar
- `VOXPIPE_EMBEDDING_MODEL_HOST_PATH`: absolute host path to the pre-provisioned model directory (mounted read-only at `/models`)
- `VOXPIPE_EMBEDDING_MODEL`: stable model identifier
- `VOXPIPE_EMBEDDING_MODEL_VERSION`: immutable artifact manifest digest in the form `sha256:<64 lowercase hex characters>`
- `VOXPIPE_EMBEDDING_DIMENSIONS=384`: explicit fixed vector-dimension contract; other values are rejected
- Optional model-specific `VOXPIPE_EMBEDDING_QUERY_PREFIX` and `VOXPIPE_EMBEDDING_DOCUMENT_PREFIX`

Then start the optional service with `docker compose --profile retrieval up -d --build`. The sidecar installs the optional `requirements-embedding.txt` dependencies, loads from the mounted local directory with `local_files_only=True`, and never downloads model weights. No model identifier/version means retrieval stays disabled; a partial model configuration prevents the service from starting. The Go API returns `503 RETRIEVAL_NOT_CONFIGURED` rather than silently falling back to a remote provider or pretending that vector retrieval is active. It also returns `503 RETRIEVAL_INDEX_NOT_READY` when the authenticated tenant has no indexed segments for the configured model/version; lexical-only results are not silently presented as hybrid search.

Indexing is explicit, not automatic on transcript persistence: ingestion must remain usable when retrieval is disabled and must not trigger hidden embedding compute. Invoke the authenticated index operation when a revision should enter the vector index. Create a separate API token/OIDC grant with `transcript:index` for indexing; do not broaden the ordinary ingestion token. Indexing is bounded to 5,000 segments per revision and embeds in batches of 64. The PostgreSQL upsert is idempotent, so a retry after partial progress safely overwrites the same tenant/revision/segment/model-version keys. Model replacement requires a new immutable version and explicit re-indexing. Search remains unavailable for that tenant/model-version pair until at least one revision is indexed; obsolete versions can be removed with the existing tenant-scoped cleanup adapter.

The internal service uses the same least-privilege PostgreSQL application role and the existing forced tenant RLS. The sidecar serializes local model inference to avoid concurrent access to one in-process model. The optional retrieval profile is not enabled by the default local quickstart.

## Quality boundary

This API makes the existing retrieval implementation usable; it does not establish that any model is suitable for Persian production search. No default model is selected, no hosted embedding provider is enabled, and no relevance-quality score is claimed. Select a model only after model-specific Persian evaluation, latency/cost measurements and a human-judged relevance set.

