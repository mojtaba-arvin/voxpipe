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
- [ ] Reconcile staged-source expiry and enforce retention/deletion/audit propagation (VP-0018).
- [ ] Add production identity, per-tenant quotas, rate limits, and upload deadlines (VP-0019).
- [ ] Harden worker supervision, lease-loss handling, and queue observability (VP-0020).

**Exit gate:** An audio asset can be ingested, validated, stored, processed asynchronously, and traced end-to-end with deterministic tests.

## Phase 2 — Speech Intelligence

- Define transcript, segment, speaker, timing, and confidence contracts.
- Implement provider-neutral STT adapter.
- Evaluate VAD, diarization, language identification, and enhancement independently.
- Build multilingual evaluation datasets with consent and provenance.
- Benchmark local and hosted providers under reproducible conditions.

**Exit gate:** Speech outputs are timestamped, attributable where supported, quality-scored, and traceable to source audio.

## Phase 3 — Structured Intelligence

- Define user/domain profile and extraction schema contracts.
- Implement schema compilation and versioning.
- Extract entities, events, topics, relations, and action items.
- Preserve evidence spans and source references for every extracted claim.
- Add deterministic validation and semantic quality gates.

**Exit gate:** Structured outputs pass schema and provenance checks, with measurable extraction quality.

## Phase 4 — Knowledge and Retrieval

- Define document/chunk/index lifecycle and tenancy boundaries.
- Implement lexical, vector, and hybrid retrieval ports.
- Evaluate PostgreSQL/pgvector and dedicated vector engines on representative workloads.
- Add filters for tenant, source, time, speaker, and entity.
- Expose stable API and MCP capabilities with authorization.

**Exit gate:** Queries return relevant, permission-filtered results with provenance and repeatable retrieval metrics.

## Phase 5 — Adaptive Model Intelligence

- Introduce model/provider registry and capability metadata.
- Add policy-based routing, semantic cache, and bounded fallback.
- Add structural and semantic quality gates.
- Measure latency, cost, availability, and quality per task/model.
- Calibrate routing from evaluation evidence; keep policy changes auditable.

**Exit gate:** Routing improves a declared quality/cost/latency objective on a versioned evaluation set without violating quality floors.

## Phase 6 — Autonomous Operations

- Add durable workflow orchestration and agent task execution.
- Add bounded self-healing for known transient failures.
- Detect regressions and open evidence-rich remediation tasks.
- Automate dependency/security updates and compatibility checks.
- Add canary, rollback, and release verification workflows.

**Exit gate:** Routine maintenance and recovery can be performed autonomously within explicit blast-radius and spending limits.

## Cross-phase quality gates

Security, privacy, accessibility, observability, reliability, cost controls, and documentation are continuous requirements—not deferred features.
