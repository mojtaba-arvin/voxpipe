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
- [x] Expose authenticated ingestion and asset-metadata capabilities through MCP (VP-0016 accepted; current search is metadata-only). Tenant-scoped lexical transcript search is delivered in Phase 4; hybrid/vector semantic search remains planned.
- [x] Reconcile staged-source expiry and enforce retention/deletion/audit propagation (VP-0018; upload leases, tenant audit events, terminal staging cleanup, configurable asset expiry; CI run 44469883406).
- [x] Add production identity, per-tenant quotas, rate limits, and upload deadlines (VP-0019).
- [x] Harden worker supervision, lease-loss handling, and queue observability (VP-0020).

**Exit gate:** An audio asset can be ingested, validated, stored, processed asynchronously, and traced end-to-end with deterministic tests.

## Phase 2 — Speech Intelligence

- [x] Define transcript, segment, speaker, timing, confidence, and provenance contracts (VP-0021; schema, provider-neutral port, validator, and offline contract tests completed).
- [x] Establish the provider-neutral STT port and local Faster-Whisper adapter (VP-0021/0022; pinned 100-item FLEURS Persian run: normalized WER 52.24%, CER 16.43%, RTF 0.1652; reproducible but below production quality).
- [x] Connect speech transcription to durable workflows (PR #73; local and hosted adapters use the same handler, immutable transcript persistence, verified audio integrity, stable workflow idempotency keys and lease heartbeats).
- [x] Add a vendor-specific hosted STT adapter behind explicit opt-in (PR #99 / ADR-0035: Deepgram Nova-3 multilingual, canonical transcript mapping, bounded HTTPS request/response, fake-transport tests, redacted error mapping, and conservative non-retryable ambiguous timeout). No API key or live call is claimed.
- [ ] Verify hosted-provider activation and measured quality/latency/cost (blocked by the current no-key / USD 0 test budget; the adapter remains disabled and zero-cost routing excludes it).
- [ ] Complete shared circuit state, durable provider retry/reconciliation, production OpenTelemetry exporter/resource configuration, dashboards, alerts and staging SLO verification (ADR-0023).
- [ ] Evaluate and implement VAD independently (VP-0023; WebRTC/Silero compared on 60 FLEURS utterances, but labels are utterance-envelope proxies rather than frame-level ground truth).
- [ ] Evaluate speaker diarization with labelled audio and DER/JER (VP-0024; controlled six-clip synthetic Neyshekar split is reproducible, but natural overlapping conversation and explicit consent evidence remain missing).
- [x] Implement inventory-aware language identification with calibrated abstention (VP-0025; PR #74; pinned FLEURS validation/test split of 300+300 samples: selective accuracy 1.0, test coverage 0.92, abstention 0.08, ECE≈0.0226. Code-switching and noisy production speech remain untested).
- [ ] Establish consent/provenance-managed corpus governance (VP-0026; manifest validation and independent annotation agreement/disagreement diagnostics are implemented; qualified annotators, consented natural recordings and third-party adjudication remain).
- [ ] Persian benchmark protocol and corpus manifest prepared; exact immutable dataset revisions, downloads, validation and real inference are still required (`docs/research/persian-stt-benchmark.md`, `benchmarks/corpora/persian.yaml`).
- [ ] Benchmark local and hosted providers under reproducible conditions; hosted execution requires explicit data-egress and spend approval.

**Exit gate:** Speech outputs are timestamped, attributable where supported, quality-scored, and traceable to source audio.

## Phase 3 — Structured Intelligence

- [x] Define versioned extraction schemas and evidence/provenance contracts (VP-0027; typed v1 schemas, immutable claim contracts, exact Unicode evidence offsets, provenance validation, and regression tests are implemented; see the work ledger and CI evidence).
- [ ] Implement structured extraction pipeline and quality gates (VP-0028; PR #77 merges evidence-derived entity/relation constraints, source-resolved evidence spans, action/event fidelity and prompt-injection hardening. Exact-head local Qwen3 4B smoke run 45325420645 passed: 4/4 fixtures, 13 validated claims, evidence validation 1.0, injection gate passed, exact-JSON micro/macro F1 1.0. The 4/4 fixtures are synthetic integration cases, not human-reviewed Persian quality metrics; Issue #32 gold-set annotation remains outstanding).

**Exit gate:** Structured outputs pass schema and provenance checks, with measurable extraction quality.

## Phase 4 — Knowledge and Retrieval

- [x] Implement tenant-isolated transcript persistence and lifecycle (VP-0029; schema, forced RLS, append-only grants, source-asset deletion cascade, atomic revision create/read APIs, identical-retry idempotency, conflicting-content rejection, concurrent-writer and cross-tenant write/read tests; GitHub Actions run 44805144386 passed).
- [x] Expose tenant-scoped lexical transcript search through Go HTTP API and MCP with a separate `transcript:read` scope, Persian query normalization, bounded pagination, transcript/asset/revision/language/speaker/time filters, PostgreSQL RLS and safe provenance (PR #111).
- [ ] Complete lexical search quality gates (VP-0030; ranking edge cases and broader update/deletion behavior still need coverage).
- [ ] Implement hybrid/semantic retrieval with measured relevance and tenant isolation (VP-0031).
- Evaluate PostgreSQL/pgvector and dedicated vector engines on representative workloads.

**Exit gate:** Queries return relevant, permission-filtered results with provenance and repeatable retrieval metrics.

## Phase 5 — Adaptive Model Intelligence

- [x] Build model/provider capability registry (VP-0032; immutable typed capability metadata now includes language/features, locality, cost/audio limits, explicit remote data-egress and deployment requirements, deprecation/replacement and rollback metadata. Remote egress and deprecated-model discovery fail closed by default; no guessed provider claims are shipped).
- [ ] Implement policy-based adaptive routing with quality floors, privacy and budget controls (VP-0033; evidence-based routing and process/PostgreSQL breaker composition are implemented; PR #127 adds crash-recoverable five-minute PostgreSQL half-open probe leases and exact-head CI coverage. Still requires deployment with verified migration/grants and outage alerts, measured task-level routing evidence, and authorized provider timeout/ambiguous-side-effect reconciliation).
- Add structural and semantic quality gates.
- Measure latency, cost, availability, and quality per task/model.
- Calibrate routing from evaluation evidence; keep policy changes auditable.

**Exit gate:** Routing improves a declared quality/cost/latency objective on a versioned evaluation set without violating quality floors.

## Phase 6 — Autonomous Operations

The first PostgreSQL-native workflow journal is a bounded sequential proof of concept that complements the ingestion queue; it is not yet the final orchestration-engine selection. See [durable workflow architecture](architecture/durable-workflows.md) and [ADR-0034](decisions/ADR-0034-postgres-native-workflows.md).

- [x] Implement durable workflow orchestration (VP-0034; PostgreSQL 16 tenant-RLS journal, bounded retries, lease fencing, cancellation, approval pause/resume, worker and STT handler, replay-safe transcript reuse, cross-instance lease reclaim, 16-worker unique claims, repeated 8-worker contention across 64 workflows, graceful worker stop and disposable PostgreSQL backup/restore with tenant-RLS verification are covered by GitHub Actions. ADR-0034 records the PostgreSQL-native decision and alternatives. Hardware-specific throughput, host-level systemd drills, production backup RPO/RTO and production restore exercises are explicitly outside this repository milestone; the optional benchmark harness is non-gating).
- [x] Add bounded self-healing for known transient repository/lease failures (PR #108; credential-safe logs and capped exponential backoff).
- Detect regressions and open evidence-rich remediation tasks.
- [x] Implement privacy-safe observability and CI-verified alert rules (VP-0035; tenant-safe aggregates, content-free STT events, OTLP SDK bootstrap, pinned Collector/Prometheus config, synthetic OTLP delivery, Prometheus scrape and promtool alert-firing tests pass in GitHub Actions. Live notification delivery and staging SLO measurement are deployment-specific and non-gating).
- [ ] Implement release, canary and rollback automation (VP-0036; build/SBOM/checksum/provenance workflow is implemented. Remaining CI gates are an actual workflow_dispatch artifact-attestation run and an isolated canary/rollback simulation; production traffic rollout is out of scope).
- [ ] Add bounded autonomous maintenance workflows (VP-0037; dependency/security automation is PR-only. Remaining gates are protected-branch review/check verification and deterministic safe-stop/rollback tests; no auto-approval or general autonomous coding agent).
- Automate dependency/security updates and compatibility checks.

**Exit gate:** Routine maintenance and recovery can be performed autonomously within explicit blast-radius and spending limits.

## Cross-phase quality gates

Security, privacy, accessibility, observability, reliability, cost controls, and documentation are continuous requirements—not deferred features.


## CI-only milestone policy (2026-11-25)

Repository milestone acceptance must be reproducible through GitHub Actions and repository-controlled fixtures. Do not leave a milestone blocked on a user-run command, a private/target hardware benchmark, a separately provisioned staging host, a production credential, a live customer integration, or a manually performed systemd drill. Replace such evidence with deterministic PostgreSQL/service integration tests, disposable containers, fault injection, workflow simulations, and GitHub artifact/attestation verification where these provide a valid proof. If no safe CI equivalent exists, explicitly exclude the deployment-specific claim from the repository milestone and document the limitation; do not claim that CI proves production deployment, real-world capacity, external alert delivery, or customer traffic behavior.

VP-0034 is complete under this policy based on the existing CI recovery, concurrency, graceful-stop and backup/restore proofs. Its hardware throughput matrix remains optional diagnostic tooling and is not a required task. VP-0035 through VP-0037 remain open only for repository-verifiable CI work described in the work ledger.
