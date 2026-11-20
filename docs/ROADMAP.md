# VoxPipe Roadmap

This roadmap is an executable sequence of phase gates, not a promise of dates. Scope may be reordered when evidence justifies it; changes must be recorded.

## Phase 0 — Autonomous Engineering Foundation

**Goal:** Make repository work resumable, verifiable, and safe for independent agents.

- [x] Establish repository identity and agent operating rules.
- [x] Create a machine-readable work ledger.
- [x] Add dependency/cycle/state validation for the ledger.
- [x] Add CI to run repository validation on every pull request.
- [x] Define contribution and pull-request templates.
- [x] Define versioning, compatibility, and release policy.
- [x] Define threat model, data classification, and audio retention defaults (design-stage requirements documented; implementation gates remain for each feature).
- [x] Compare orchestration approaches for durable agent/task execution (selection deferred to a measured proof of concept).
- [x] Create a reproducible local development environment and validator test suite.
- [x] Add repository-scoped skills, specialist read-only agents, and an auditable agent execution protocol.

**Exit gate:** A clean checkout can validate itself; a new agent can identify the next unblocked task, execute it, and leave auditable progress without relying on chat history.

## Phase 1 — Audio Ingestion Foundation

- [x] Define canonical audio asset and session contracts.
- [x] Evaluate media probing and normalization tools before selecting an implementation (ffprobe isolated subprocess is the PoC primary; no binary is approved for distribution yet).
- [x] Implement batch ingestion orchestration with durable session metadata and lease-based resumable recovery; upload API remains separate.
- [x] Validate media type, duration, size, sample rate, and channel layout from actual file bytes (not client hints).
- [x] Add a separate full-file decode integrity gate; metadata probing alone does not prove that all audio packets decode.
- [x] Add object-storage abstraction and local filesystem development implementation.
- [x] Add lease-based stale-session recovery and failed/expired-session orphan reconciliation.
- [x] Add PostgreSQL metadata repository, versioned migrations, and forced tenant RLS.
- [x] Complete authenticated batch upload API and PostgreSQL-backed durable job queue (VP-0015 accepted; Python and Go/PostgreSQL CI passed on the implementation head).
- [x] Implement queue cancellation, bounded retries, dead-letter handling, and tenant-scoped job status.
- [x] Expose authenticated ingestion and asset-metadata capabilities through MCP (VP-0016 accepted; current search is metadata-only). Transcript and semantic search remain planned for Phase 4.
- [x] Reconcile staged-source expiry and enforce retention/deletion/audit propagation (VP-0018; upload leases, tenant audit events, terminal staging cleanup, configurable asset expiry; CI run 44469883406).
- [x] Add production identity, per-tenant quotas, rate limits, and upload deadlines (VP-0019).
- [x] Harden worker supervision, lease-loss handling, and queue observability (VP-0020).

**Exit gate:** An audio asset can be ingested, validated, stored, processed asynchronously, and traced end-to-end with deterministic tests.

## Phase 2 — Speech Intelligence

- [x] Define transcript, segment, speaker, timing, confidence, and provenance contracts (VP-0021; schema, provider-neutral port, validator, and offline contract tests completed).
- [ ] Evaluate STT providers and implement the first selected adapter (VP-0022; faster-whisper selected; offline adapter and scorer exist; real Persian corpus inference remains pending).
- [ ] Evaluate and implement VAD independently (VP-0023).
- [ ] Evaluate speaker diarization with labelled audio and DER/JER (VP-0024).
- [ ] Implement language identification with calibrated abstention (VP-0025).
- [ ] Establish consent/provenance-managed corpus governance (VP-0026).
- [ ] Persian benchmark protocol and corpus manifest prepared; exact immutable dataset revisions, downloads, validation and real inference are still required (`docs/research/persian-stt-benchmark.md`, `benchmarks/corpora/persian.yaml`).
- [ ] Benchmark local and hosted providers under reproducible conditions; hosted execution requires explicit data-egress and spend approval.

**Exit gate:** Speech outputs are timestamped, attributable where supported, quality-scored, and traceable to source audio.

## Phase 3 — Structured Intelligence

- [ ] Define versioned extraction schemas and evidence/provenance contracts (VP-0027).
- [ ] Implement structured extraction pipeline and quality gates (VP-0028).

**Exit gate:** Structured outputs pass schema and provenance checks, with measurable extraction quality.

## Phase 4 — Knowledge and Retrieval

- [ ] Implement tenant-isolated transcript persistence and lifecycle (VP-0029).
- [ ] Implement lexical transcript search (VP-0030).
- [ ] Implement hybrid/semantic retrieval with measured relevance and tenant isolation (VP-0031).
- Evaluate PostgreSQL/pgvector and dedicated vector engines on representative workloads.

**Exit gate:** Queries return relevant, permission-filtered results with provenance and repeatable retrieval metrics.

## Phase 5 — Adaptive Model Intelligence

- [ ] Build model/provider capability registry (VP-0032).
- [ ] Implement policy-based adaptive routing with quality floors, privacy and budget controls (VP-0033).
- Add structural and semantic quality gates.
- Measure latency, cost, availability, and quality per task/model.
- Calibrate routing from evaluation evidence; keep policy changes auditable.

**Exit gate:** Routing improves a declared quality/cost/latency objective on a versioned evaluation set without violating quality floors.

## Phase 6 — Autonomous Operations

- [ ] Implement durable workflow orchestration (VP-0034).
- Add bounded self-healing for known transient failures.
- Detect regressions and open evidence-rich remediation tasks.
- [ ] Add production alerting and SLOs (VP-0035).
- [ ] Implement release, canary and rollback automation (VP-0036).
- [ ] Add bounded autonomous maintenance workflows (VP-0037).
- Automate dependency/security updates and compatibility checks.

**Exit gate:** Routine maintenance and recovery can be performed autonomously within explicit blast-radius and spending limits.

## Cross-phase quality gates

Security, privacy, accessibility, observability, reliability, cost controls, and documentation are continuous requirements—not deferred features.
