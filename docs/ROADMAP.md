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
- Hosted-provider live activation and measured quality/latency/cost are explicitly non-gating: Deepgram remains `need_key`, remote egress is opt-in, and paid-test budget is USD 0.
- Shared breaker foundations, provider retry safety and CI-verified telemetry/alert evaluation are implemented. Live deployment wiring, vendor idempotency/reconciliation, dashboards and staging SLOs remain deployment-specific follow-ups, not repository milestone gates.
- [x] Implement provider-neutral VAD contracts, validated Silero/WebRTC adapters, deterministic evaluator and reproducible public-proxy comparison (VP-0023; FLEURS envelope labels are explicitly not frame-level ground truth; frame-labelled Persian quality work is non-gating).
- [x] Implement provider-neutral diarization contracts, validated ECAPA baseline, overlap capability declaration and deterministic DER/JER evaluation on a controlled held-out public split (VP-0024; natural consented overlap evaluation remains a non-gating quality follow-up).
- [x] Implement inventory-aware language identification with calibrated abstention (VP-0025; disjoint FLEURS validation/test evaluation and regression tests are in CI. Code-switching, overlap and noisy production speech remain non-gating quality follow-ups).
- [x] Establish corpus provenance and governance tooling (VP-0026; versioned manifests, license/consent-basis metadata, checksums, public/customer separation, deletion/redistribution policy and independent annotation diagnostics are implemented. Private consent collection and adjudication are external quality-program activities).
- [ ] Persian benchmark protocol and corpus manifest prepared; exact immutable dataset revisions, downloads, validation and real inference are still required (`docs/research/persian-stt-benchmark.md`, `benchmarks/corpora/persian.yaml`).
- [ ] Benchmark local and hosted providers under reproducible conditions; hosted execution requires explicit data-egress and spend approval.

**Repository exit gate (complete):** Speech contracts, adapters, validation, reproducible public-proxy evaluation and provenance are implemented and CI-verified. Real-world quality on consented natural speech remains an explicitly unclaimed product-quality follow-up.

## Phase 3 — Structured Intelligence

- [x] Define versioned extraction schemas and evidence/provenance contracts (VP-0027; typed v1 schemas, immutable claim contracts, exact Unicode evidence offsets, provenance validation, and regression tests are implemented; see the work ledger and CI evidence).
- [x] Implement structured extraction pipeline and deterministic safety gates (VP-0028; provider-neutral typed extraction, source-resolved evidence spans, prompt-injection isolation, append-only tenant-RLS persistence and four-fixture Qwen3 smoke are verified. Synthetic exact-match scores are integration evidence only, not human-reviewed Persian quality metrics).

**Repository exit gate (complete):** Structured outputs pass schema, evidence and provenance checks; deterministic synthetic integration tests verify safety. Human-reviewed semantic quality remains a separate, non-gating product-quality follow-up.

## Phase 4 — Knowledge and Retrieval

- [x] Implement tenant-isolated transcript persistence and lifecycle (VP-0029; schema, forced RLS, append-only grants, source-asset deletion cascade, atomic revision create/read APIs, identical-retry idempotency, conflicting-content rejection, concurrent-writer and cross-tenant write/read tests; GitHub Actions run 44805144386 passed).
- [x] Expose tenant-scoped lexical transcript search through Go HTTP API and MCP with a separate `transcript:read` scope, Persian query normalization, bounded pagination, transcript/asset/revision/language/speaker/time filters, PostgreSQL RLS and safe provenance (PR #111).
- [x] Complete tenant-scoped lexical transcript search and deterministic query-normalization regression coverage (VP-0030; see work ledger and CI evidence).
- [x] Implement tenant-scoped hybrid/semantic retrieval foundations (VP-0031; PostgreSQL FTS + pgvector, deterministic RRF, Persian normalization, provenance, RLS, deletion and evaluator tests. Human-judged relevance metrics and production model selection are non-gating quality follow-ups).
- PostgreSQL/pgvector was selected and implemented; evaluating dedicated vector engines is an optional future architecture exercise, not a milestone gate.

**Repository exit gate (complete):** Retrieval enforces tenant permissions and provenance with deterministic evaluator coverage. Human-judged relevance metrics and production embedding selection remain non-gating quality follow-ups.

## Phase 5 — Adaptive Model Intelligence

- [x] Build model/provider capability registry (VP-0032; immutable typed capability metadata now includes language/features, locality, cost/audio limits, explicit remote data-egress and deployment requirements, deprecation/replacement and rollback metadata. Remote egress and deprecated-model discovery fail closed by default; no guessed provider claims are shipped).
- [x] Implement policy-based adaptive routing with quality floors, privacy and budget controls (VP-0033; allowlisted routing requires fresh measured task-quality evidence and enforces availability, quality, latency, locality and cost constraints; bounded deterministic fallbacks and content-free audit metadata are covered by tests. Process-local and opt-in PostgreSQL breakers are integrated; PR #127 adds crash-recoverable five-minute half-open probe leases with stale-token fencing. Exact-head and post-merge CI evidence is recorded in the work ledger. Live hosted-provider activation, production migration/grants, external outage-alert delivery and real-workload quality optimization remain deployment/provider follow-ups, not repository acceptance gates; no hosted provider is enabled and the test budget remains USD 0).
- Add structural and semantic quality gates.
- Measure latency, cost, availability, and quality per task/model.
- Calibrate routing from evaluation evidence; keep policy changes auditable.

**Repository exit gate (complete):** Routing decisions enforce declared capability, evidence freshness, privacy/egress, cost and breaker policies with deterministic tests. Production task-quality optimization requires separately supplied evaluation data and is not claimed.

## Phase 6 — Autonomous Operations

The first PostgreSQL-native workflow journal is a bounded sequential proof of concept that complements the ingestion queue; it is not yet the final orchestration-engine selection. See [durable workflow architecture](architecture/durable-workflows.md) and [ADR-0034](decisions/ADR-0034-postgres-native-workflows.md).

- [x] Implement durable workflow orchestration (VP-0034; PostgreSQL 16 tenant-RLS journal, bounded retries, lease fencing, cancellation, approval pause/resume, worker and STT handler, replay-safe transcript reuse, cross-instance lease reclaim, 16-worker unique claims, repeated 8-worker contention across 64 workflows, graceful worker stop and disposable PostgreSQL backup/restore with tenant-RLS verification are covered by GitHub Actions. ADR-0034 records the PostgreSQL-native decision and alternatives. Hardware-specific throughput, host-level systemd drills, production backup RPO/RTO and production restore exercises are explicitly outside this repository milestone; the optional benchmark harness is non-gating).
- [x] Add bounded self-healing for known transient repository/lease failures (PR #108; credential-safe logs and capped exponential backoff).
- Future enhancement (non-gating): automatically detect additional regression classes and open evidence-rich remediation tasks.
- [x] Implement privacy-safe observability and CI-verified alert rules (VP-0035; tenant-safe aggregates, content-free STT events, OTLP SDK bootstrap, pinned Collector/Prometheus config, synthetic OTLP delivery, Prometheus scrape and promtool alert-firing tests pass in GitHub Actions. Live notification delivery and staging SLO measurement are deployment-specific and non-gating).
- [x] Build verifiable release artifacts and provenance (VP-0036; reusable release-candidate workflow ran successfully on main, built Linux amd64/arm64 binaries, CycloneDX SBOM, manifest and checksums, verified GitHub attestations for every artifact, and uploaded the release bundle. Production canary/rollback and customer traffic are deployment concerns, not repository gates).
- [x] Add bounded autonomous maintenance workflows (VP-0037; grouped Dependabot and vulnerability checks remain PR-only; main ruleset requires PRs and strict Python/Go checks, resolves review threads, blocks deletion/force-push and has no bypass. Durable workflow actions support actor-attributed human approval, rejection and cancellation; SIGTERM/SIGINT safe-stop is tested. Reverts remain auditable PRs; no auto-approval, auto-merge or general autonomous coding agent).
- Dependency/security updates and compatibility checks are automated through PR-based workflows; direct pushes and auto-merge remain disabled.

**Repository exit gate (complete):** Maintenance proposals are PR-based, protected by required checks, and durable operations have actor-attributed approval, cancellation and safe-stop controls. This does not claim a general autonomous coding agent or production approval service.

## Cross-phase quality gates

Security, privacy, accessibility, observability, reliability, cost controls, and documentation are continuous requirements—not deferred features.


## CI-only milestone policy (2026-11-25)

Repository milestone acceptance must be reproducible through GitHub Actions and repository-controlled fixtures. Do not leave a milestone blocked on a user-run command, a private/target hardware benchmark, a separately provisioned staging host, a production credential, a live customer integration, or a manually performed systemd drill. Replace such evidence with deterministic PostgreSQL/service integration tests, disposable containers, fault injection, workflow simulations, and GitHub artifact/attestation verification where these provide a valid proof. If no safe CI equivalent exists, explicitly exclude the deployment-specific claim from the repository milestone and document the limitation; do not claim that CI proves production deployment, real-world capacity, external alert delivery, or customer traffic behavior.

VP-0034 is complete under this policy based on the existing CI recovery, concurrency, graceful-stop and backup/restore proofs. Its hardware throughput matrix remains optional diagnostic tooling and is not a required task. VP-0032 through VP-0037 are complete for repository scope under this policy. Deployment-specific capacity, production credentials, live customer traffic, staging notification delivery and production restore objectives are explicitly excluded; CI does not imply production readiness.


## Repository milestone closure (2026-11-25)

All 46 repository work items (VP-0001 through VP-0046) are complete. Completion means the repository implementation, contracts, deterministic tests and CI evidence meet the revised repository-scoped acceptance criteria. It does **not** mean that every model is production-quality or that deployment-specific validation has occurred.

The following remain non-gating product/deployment follow-ups: frame-labelled Persian VAD data; consented natural overlapping-speech diarization data; code-switch/noisy-field language-ID evaluation; a privately sourced and adjudicated Persian extraction gold set; a human-judged Persian retrieval relevance set and selected production embedding model; live Deepgram activation (no key, USD 0 test budget); target-specific capacity and host operations; production backup/PITR/RPO/RTO; live alert delivery, staging SLOs and customer-traffic canary/rollback. Do not fabricate these data or claims to change a repository milestone status.


## Phase 7 — Product Experience

**Goal:** Turn the repository's API and worker capabilities into a practical workflow that a developer or operator can use without writing a bespoke integration.

- [x] Deliver a first-party CLI for batch audio ingestion, durable job status, and transcript search (VP-0038; streamed uploads, incremental SHA-256, existing authenticated API boundaries, bounded polling, and five-job CI validation in run 45576085075).
- [x] Add a guided local quickstart that proves API + worker + PostgreSQL + sample audio flow end-to-end without hosted-provider credentials or paid services (VP-0039; PR #142 merged as c9a9122; exact-head CI run 45581360168 passed all six jobs, including the full Docker Compose sample-ingestion flow).
- [x] Expose hybrid retrieval through a versioned authenticated API/MCP contract (VP-0040; PR #146 merged as `97df157`; exact-head CI run `45589072997` and post-merge main CI run `45589337613` passed all six jobs). The private local sidecar, explicit idempotent revision indexing, transcript:read/transcript:index scopes, immutable SHA-256 model version, explicit 384-dimension contract, and fail-closed readiness behavior are in `main`.
- [x] Deliver a tenant-scoped operational summary API and first-party CLI health view (VP-0041; PR #144 merged as `f353875`; exact-head CI run `45590134481` and post-merge main CI run `45590454724` passed all six jobs). Requires dedicated `operations:read`, bounds activity windows to seven days, exposes queue age/retry/dead-letter aggregates only, preserves tenant RLS, and does not create metrics snapshots or extend retention.

**Product boundary:** These are additive product-experience milestones, not a reason to reopen completed repository implementation items. Do not select a production embedding model, enable remote data egress, or claim real Persian quality without the corresponding evidence and approvals.


## Phase 7 closure (2026-11-25)

VP-0038 through VP-0041 are complete for repository scope:
- VP-0038 first-party CLI: PR #141, merge commit `e79d797`.
- VP-0039 guided local quickstart: PR #142, merge commit `c9a9122`.
- VP-0040 authenticated hybrid retrieval API/MCP: PR #146, merge commit `97df157`.
- VP-0041 tenant-scoped operational summary API/CLI: PR #144, merge commit `f353875`.

Completion is backed by exact-head CI and post-merge CI. It does not establish production deployment readiness, external alert delivery, a production STT provider, or human-validated Persian quality. The remaining external evidence gates are issues #32 (adjudicated Persian extraction gold set), #47 (consented natural-conversation speech corpus), and #48 (human-judged retrieval relevance). Do not fabricate evidence or close those issues without the required consented data and human adjudication.

## Phase 8 — Operational Readiness

**Goal:** Make runtime dependencies and failure states observable to deployment tooling without confusing repository evidence with production proof.

- [x] Add unauthenticated, content-free API liveness/readiness probes; verify PostgreSQL connectivity and core schema; wire an image healthcheck and gate local worker startup on API readiness (VP-0042; PR #148; exact-head CI run `45606622379` passed all six jobs, including 474 Python tests, Go/PostgreSQL integration and Docker Compose sample-ingestion E2E).
- [x] Add tenant-scoped worker process heartbeats and privacy-safe active/stale/idle/busy aggregates to the operations API; keep process liveness independent from job-lease renewal and record busy age without exposing worker IDs (VP-0043; PR #149 merged as `eee1bf3`; exact-head CI run `45612304573` passed all six jobs and post-merge main validation run `45613094434` passed all six jobs).
- [x] Add optional shared S3-compatible object storage behind the existing object-store port; preserve local storage as the default and enforce tenant-scoped immutable keys, checksum verification, bounded retries and explicit encryption (VP-0044; PR #152 merged as `ee19b92`; exact-head CI run `45615557588` passed all seven jobs, dependency audit run `45615557541` passed both audits, and post-merge main validation run `45615847663` passed all seven jobs).
- [x] Replace the shared POSIX upload-staging dependency with a versioned S3-backed contract between the Go API and Python worker; preserve local staging as the default, verify tenant/size/SHA-256, retain retryable sources and reconcile terminal staging (VP-0045; core PR #155 merged as `ccbe90c`; exact-head CI run `45624282856` passed all seven jobs, dependency audit `45624282950` passed both audits, and post-merge main validation run `45624632771` passed all seven jobs; separate-container E2E PR #156 run `45625810038` passed all seven jobs and proved public API upload, Python queue processing, staging deletion and durable-object retention without a shared volume; post-merge main validation run `45627617352` passed all seven jobs).

**Operational boundary:** In S3-compatible mode, the Go API and Python worker have been verified in separate containers without a shared POSIX staging or object volume (VP-0045). Local filesystem staging remains the default for the guided quickstart and single-node development. Worker heartbeats show tenant-scoped process liveness, not semantic processing progress or automated paging. API readiness, worker heartbeats and disposable Silo integration do not prove production cloud durability, real-account IAM isolation, queue drain SLOs, retrieval model availability, backup RPO/RTO, multi-region replication or customer-traffic capacity.

**Completed staging milestone — VP-0045 (Issue #153):** PR #155 merged the S3-backed staging protocol and PR #156 added the separate-container E2E. Exact-head validation run `45625810038` passed all seven jobs; the test uploaded and processed a generated WAV through the Go API and Python worker, then proved terminal reconciliation removed the staging object while retaining the durable audio object with its checksum. See [ADR-0045](decisions/ADR-0045-shared-upload-staging.md). Local staging remains the quickstart default.

**Phase 8 complete — VP-0042 through VP-0046:** VP-0046 (issue #157) is complete for repository scope. PR #158 (`0ee0756`) separates API staging and worker durable-object credentials and proves denied durable-object access (7/7 exact-head CI). PR #159 (`a22c7e6`) adds content-free dependency readiness, lifecycle safety validation and separate versioned-bucket recovery (7/7 exact-head CI and 7/7 post-merge main CI). PR #160 (`0c61824`) adds transient read and cleanup-retry fault injection (7/7 exact-head CI). Post-merge main run `45641155682` passed all seven jobs. The active tenant-object bucket remains unversioned until hard-delete semantics cover historical versions. See [issue #157](https://github.com/mojtaba-arvin/voxpipe/issues/157) and [ADR-0046](decisions/ADR-0046-s3-deployment-recovery.md).

**Next product work:** Automated evaluation is now the next repository phase. Human gold and natural-conversation evidence remain separately gated; neither may be simulated to close the product follow-ups.

## Phase 9 — Automated Evaluation Baselines (M1)

**Goal:** Build reproducible, repository-controlled silver evaluation paths for extraction, retrieval and speech, with clear provenance and no paid inference dependency.

- [x] Add the manual evaluation workflow skeleton, optional disclosure helper and always-run aggregation structure (VP-0047 / issue #164; PR #187 merged; exact-head CI run #45674932520 passed all seven jobs).
- [x] Define separate conversation/retrieval schemas and a bounded `CORPUS_PATH` loader with a five-record synthetic placeholder (VP-0050 / issue #167; PR #188 merged; exact-head CI run #45675967734 passed all seven jobs).
- [x] Speed up dependency installation and add static model caching where useful (VP-0048 / issue #165; PRs #199 and #204 merged; exact-head run #45751421748 measured uv 0.13.10 cold install 0.6080s and warm install 0.0793s, 7.6671x for 20 packages. This is install-cache evidence, not a total CI speedup claim).
- [x] Measure CPU feasibility for the selected ASR model before enabling heavyweight model jobs (VP-0049 / issue #166; PR #201; exact-head smoke run #45748520630 used a 15.0512s publisher English sample on 4 CPU cores, RTF 0.5084, peak RSS ~6.2 GiB. This is English CPU feasibility, not Persian quality evidence).
- [x] Complete deterministic extraction generation, silver labels and micro/macro metrics (VP-0051–VP-0053 / issues #168–#170; PR #189 merged; exact-head CI run #45678455375 passed).
- [x] Wire and verify the `eval-32` job, artifact upload and disclosure to #32 (VP-0054 / issue #171; PR #197 exact-head evaluation run #45747200458 uploaded `extraction-silver-baseline` and posted the disclosure. 120 synthetic conversations; micro/macro F1 1.0000 is template-label agreement, not human gold or production accuracy).
- [x] Complete deterministic retrieval query generation, heuristic qrels and test-split Recall@5/MRR/nDCG@5 metrics (VP-0055–VP-0057 / issues #172–#174; PR #191 merged; exact-head CI run #45681362570 passed).
- [x] Wire and verify the `eval-48` job, artifact upload and disclosure to #48 (VP-0058 / issue #175; PR #197 exact-head evaluation run #45747200458 uploaded `retrieval-silver-baseline` and posted the disclosure. 120 generated queries over a five-passage placeholder, with heuristic qrels; metrics validate pipeline wiring only, not human relevance).
- [x] Generate Persian synthetic TTS overlap fixtures with exact synthetic timing annotations (VP-0062 / issue #179; PR #194 merged; exact-head run #45685985373 verified both pinned voice assets, generated 50 conversations and uploaded audio-free annotations/manifest. No natural-speech or distinct-speaker diarization claim).
- [x] Replace the vulnerable legacy Qwen ASR package with a pinned native Transformers evaluator (VP-0060 implementation; PR #208 merged as `54dc9c7`; exact-head validation run #45778111718 passed all nine jobs and dependency audit #45778111759 passed both jobs. The English CPU smoke is feasibility evidence only; the actual Persian sample evaluation remains blocked).
- [ ] Download a real bounded Common Voice Persian sample and record provenance (VP-0059 / issue #176). The latest MDC SDK attempt returned `FileNotFoundError`/`sample_unavailable` (an earlier attempt returned HTTP 403). No sample bytes were downloaded. Ask MDC to confirm bounded-sample publication and API-key owner/org access; do not re-enter the key, repeat terms acceptance, or fetch the 11.2 GB full release archive.
- [ ] Complete Persian Qwen3-ASR WER/CER using a real bounded Common Voice sample (VP-0060 / issue #177). The native evaluator and reusable `workflow_call` integration into `eval.yml` are merged (PR #210, commit `b4890114`); the bounded sample is currently `sample_unavailable` (an earlier attempt returned HTTP 403), so no Persian WER/CER is claimed.
- [ ] Add Persian WhisperX forced alignment; explicitly do not evaluate diarization on isolated read-speech clips (VP-0061 / issue #178). PR #212 is merged as `a6453b1`; exact-head validation passed 9/9 jobs and dependency audit passed. It adds a pinned Apache-2.0 Persian wav2vec2 revision, isolated CPU runtime, ephemeral Qwen transcript handoff, aggregate-only output and empty `speaker_segments.json`; runtime smoke run #45850122656 passed with the pinned Persian model loaded on CPU in float32 and a clean installed-environment audit. This validates model loading only; real bounded-sample alignment remains outstanding. Natural conversational diarization/overlap remains M2.
- [x] Wire the speech evaluation aggregate and disclosures, keeping read-speech, alignment and synthetic overlap metrics separate (VP-0063 / issue #180). PR #213 exact-head run #45850459347 passed: `eval-47` emitted separate read-speech/synthetic branch outcomes, a schema-valid audio-free `report_47.json`, and the #47 disclosure. Both branches were explicitly `skipped` in this PR run; the underlying Persian evidence gate remains open under #176–#178.
- [x] Aggregate all three group outcomes with exact artifact IDs/URLs, explicit success/failure/skipped states and truthful umbrella-issue comments; add completion labels only if every group succeeds (VP-0064 / issue #181). PR #213 exact-head run #45850459347 passed: `summary.json` was built and uploaded with exact provenance/artifact references, and truthful comments were posted to #32/#47/#48. Label reconciliation is restricted to explicit manual evaluation runs and requires all three validated groups to succeed.

**M1 status:** 15 of 18 work items are complete; three remain open (#176, #177 and #178). Group C aggregation and cross-group reporting are implemented, but the latest run correctly marked unavailable read-speech and synthetic branches as skipped. The external blocker is MDC bounded-sample availability. The merged English CPU smoke, Persian model-load smoke, synthetic TTS fixtures and green CI do not substitute for Persian WER/CER or real alignment coverage.

**M1 interpretation boundary:** Silver labels, synthetic templates, heuristic qrels, Common Voice read-speech and TTS overlap fixtures are pipeline evidence only. They do not establish human-reviewed Persian extraction/retrieval quality, natural conversational performance, medical readiness or production accuracy. Keep heavyweight evaluation workflows manual; no paid inference APIs or real customer data are permitted.

## Phase 10 — Human Gold Upgrade Path (M2)

**Goal:** Replace silver proxies with qualified human judgments and consented natural conversation evidence.

- [ ] Recruit two independent Persian annotators and adjudicate disagreements; report Cohen's kappa (VP-0065–VP-0066 / issues #182–#183).
- [ ] Collect human relevance judgments for retrieval queries and passages (VP-0067 / issue #184).
- [ ] Acquire real Persian conversational audio with explicit informed consent, approved privacy/legal terms and bounded retention/access (VP-0068 / issue #185).
- [ ] Validate diarization and overlap on that consented natural-audio set (VP-0069 / issue #186).

**M2 external blockers:** Recruiting annotators, securing participant consent, approving data handling and collecting human judgments require people and approvals outside repository automation. Do not fabricate those inputs, substitute synthetic/read speech, or close umbrella issues [#32](https://github.com/mojtaba-arvin/voxpipe/issues/32), [#47](https://github.com/mojtaba-arvin/voxpipe/issues/47), [#48](https://github.com/mojtaba-arvin/voxpipe/issues/48) until their actual evidence gates are met.
