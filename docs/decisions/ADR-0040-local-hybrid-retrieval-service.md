# ADR-0040: Keep local hybrid embedding inference behind a private sidecar

- **Status:** Accepted
- **Date:** 2026-11-25
- **Decision owners:** VoxPipe maintainers

## Context

VoxPipe already has provider-neutral embedding contracts, a local SentenceTransformer adapter, a PostgreSQL/pgvector semantic adapter, Persian query normalization and deterministic weighted reciprocal-rank fusion. Those primitives were not exposed through the authenticated Go API/MCP contract, and no production embedding model was selected.

Embedding inference is a Python capability in the current repository. Moving model runtime dependencies into the Go API would couple the public API binary to a Python ML stack or require a second implementation of model behavior. Loading model weights from a public request path or downloading them at runtime would also create an unsafe and non-reproducible provider boundary.

## Decision

1. Keep the Go API as the public authentication and authorization boundary.
2. Run embedding inference and hybrid orchestration in an optional private Python sidecar, reachable only over the internal service network and a dedicated shared secret.
3. The Go API derives tenant identity from the verified bearer principal and forwards it to the sidecar; public request bodies never accept tenant IDs.
4. Use only a pre-provisioned local model directory, mounted read-only. Model identifier, immutable version and the 384-dimensional contract must be explicit. No model is selected by default and runtime weight downloads are forbidden.
5. Expose hybrid search under transcript:read and idempotent transcript indexing under a dedicated transcript:index scope.
6. Reuse existing source-text SHA binding, model/version isolation, forced tenant RLS, Persian normalization and deterministic RRF.
7. If the local model service is not configured, hybrid search/indexing fails closed with a stable RETRIEVAL_NOT_CONFIGURED response. If the authenticated tenant has no vectors for the configured model version, hybrid search fails with RETRIEVAL_INDEX_NOT_READY; it never presents lexical-only results as hybrid. There is no hosted-provider fallback.
8. Transcript persistence does not automatically trigger embedding inference. Indexing is an explicit, idempotent operation through the authenticated API/MCP index command so retrieval remains optional and ingestion never incurs hidden model compute.

## Consequences

- The public Go API remains free of Python ML dependencies.
- Local quickstart remains small and does not build or activate the optional model image.
- Operators must provision model files and an internal service secret out of band.
- Indexing is explicit, synchronous and bounded to 5,000 segments per immutable revision; batches of 64 and idempotent upserts make retries safe after partial progress. Ingestion does not implicitly invoke embedding inference.
- Model versions are immutable SHA-256 artifact-manifest digests and the deployment explicitly declares the 384-dimensional contract. A separate model version creates a separate vector namespace and requires explicit re-indexing. Search stays unavailable for that tenant/version until indexing exists; existing tenant-scoped cleanup removes obsolete versions.
- The sidecar shares the restricted application database role and relies on the same forced RLS tenant context; it is not exposed on a host port.
- Model choice and Persian relevance quality remain evidence-gated and are not implied by API availability.
