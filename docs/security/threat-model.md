# VoxPipe Threat Model

**Version:** 0.1  
**Date:** 2026-11-19  
**Status:** Design-stage threat model; implementation controls are not yet verified.  
**Scope:** Planned batch-first audio ingestion, speech processing, structured extraction, retrieval, model routing, and autonomous agent operations.

> This document records design risks and required controls. A control listed here is not evidence that the control has already been implemented.

## 1. Security objectives

- Confidentiality: raw audio, transcripts, derived facts, embeddings, credentials, and tenant metadata are disclosed only to authorized principals.
- Integrity: assets, jobs, transcripts, extracted claims, indexes, and audit events cannot be silently altered or confused across tenants.
- Availability: malformed or abusive inputs cannot exhaust worker, model, storage, or provider capacity.
- Provenance: each derived fact can be traced to a source asset, time range, processing run, model/provider version, and schema version.
- Deletion: retention and deletion operations propagate to primary and derived stores according to explicit, testable guarantees.
- Bounded autonomy: agents and automated workers can perform only authorized actions within explicit resource, data, and blast-radius limits.

## 2. Assets and security classification

| Asset | Classification | Required handling |
|---|---|---|
| Raw audio/video | Highly sensitive | Encryption, tenant authorization, purpose limitation, retention/deletion |
| Transcript and speaker labels | Highly sensitive | Same controls as source audio; redact from ordinary logs |
| Extracted facts, summaries, embeddings | Sensitive derived data | Treat as potentially reversible/disclosive; preserve provenance and tenant scope |
| Jobs, sessions, workflow state | Internal/sensitive | Authorization, idempotency, integrity, bounded retention |
| API keys, provider tokens, signing secrets | Secret | Secret manager, least privilege, rotation, never log |
| Tenant IDs, user IDs, access metadata | Sensitive metadata | Access control, scoped queries, audit |
| Model prompts/responses and traces | Sensitive | Minimize content, redact, access-control, configurable retention |
| Evaluation datasets and fixtures | Sensitive unless synthetic | Consent/provenance, access control, no accidental production samples |

## 3. Actors

- **Tenant user/client:** submits audio and queries; may be compromised or malicious.
- **Tenant administrator:** manages users, policies, retention, and exports within one tenant.
- **VoxPipe operator:** operates infrastructure with restricted, audited access.
- **Internal worker/service:** processes assets and invokes providers using service identity.
- **External provider:** STT, LLM, embedding, storage, or webhook service; may fail, be compromised, or retain data contrary to expectations.
- **Autonomous engineering/operations agent:** reads repository and telemetry, changes code/configuration, and invokes tools under a bounded capability policy.
- **Attacker:** unauthenticated internet actor, malicious tenant user, compromised dependency/provider, or insider.

## 4. Trust boundaries and data flow

```text
Untrusted client/audio
       |
       v
[API authentication + authorization]
       |
       v
[Ingestion validation / quarantine] ---> [Object storage]
       |                                      |
       v                                      v
[Job/workflow state] ---> [Isolated workers / parsers]
                               |          |
                               v          v
                         [Speech providers] [LLM/model gateway]
                               |          |
                               +----+-----+
                                    v
                         [Transcript + extraction]
                                    |
                                    v
                     [Metadata DB / search indexes]
                                    |
                                    v
                    [Authorized API / MCP / SDK clients]

[Agent control plane] ---> repository / CI / telemetry / approved tools
          |
          +-- must not inherit tenant data access or production credentials by default
```

Trust boundaries exist at every arrow. A successful authentication at the API does not authorize downstream access automatically; tenant and purpose context must be propagated and independently enforced.

## 5. Threat register

Risk ratings are initial qualitative priorities, not measured probabilities. Reassess after implementation and deployment topology are known.

| ID | Threat / abuse case | Initial priority | Required mitigations | Verification evidence |
|---|---|---:|---|---|
| T-01 | Tenant escape through object IDs, metadata queries, vector filters, cache keys, or MCP tools | Critical | Derive tenant from authenticated identity; enforce database row-level controls and scoped storage paths; include tenant in cache/index keys; deny cross-tenant defaults | Cross-tenant negative tests for every read/write/search/export path; adversarial ID substitution tests |
| T-02 | Prompt injection spoken in audio or embedded in transcript/retrieved content causes agent/tool misuse | High | Treat speech and retrieved text as untrusted data; separate instructions from evidence; tool allowlists and per-tool authorization; require policy checks outside the model | Red-team corpus with spoken tool-invocation instructions; assert no unauthorized tool call or policy override |
| T-03 | Malformed media exploits codec/parser or causes worker compromise | High | Validate signatures and formats; patch codecs; isolate parsers in low-privilege sandbox/container; disable unnecessary network/filesystem access; cap CPU/memory/time | Fuzz media parsers; malformed-file corpus; verify worker isolation and resource limits |
| T-04 | Oversized/long/high-channel-count media exhausts CPU, memory, disk, or inference budget | High | Enforce upload, duration, sample-rate, channel, decompression, concurrency, and cost quotas; backpressure; cancellation; bounded queues | Load/abuse tests at and above limits; verify predictable rejection and no queue starvation |
| T-05 | Raw audio/transcript leaks to model, speech, logging, or observability provider outside approved policy | High | Provider allowlist by data class/region; explicit data minimization; transcript-only mode where possible; no content in default traces; contractual retention/training review | Egress tests with mock providers; inspect logs/traces; provider configuration review |
| T-06 | Credential or service identity compromise enables bulk audio access or paid model abuse | High | Short-lived credentials; least privilege; secret manager; rotation; per-provider budgets; anomaly alerts; separate dev/test/prod identities | Secret scanning; permission tests; rotation drill; budget exhaustion and alert tests |
| T-07 | Replay, retry, or worker crash duplicates external side effects or corrupts job state | High | Stable idempotency keys; transactional outbox/inbox where needed; explicit retry classification; bounded retry budget; durable state transitions | Fault injection between side effect and checkpoint; duplicate delivery and replay tests |
| T-08 | Webhook/session spoofing or replay changes job state | High | Signed webhook validation, timestamp/nonce/replay window, secret rotation, authenticated callback IDs, strict state machine | Invalid signature, stale timestamp, duplicate event, and out-of-order event tests |
| T-09 | Deletion succeeds in primary DB but leaves audio, transcript, embeddings, cache, replicas, or provider copies | High | Asset lineage/deletion graph; durable deletion workflow; tombstones; retry/reconciliation; explicit backup/provider retention disclosure | End-to-end deletion test across every store; periodic orphan scan; documented backup expiry |
| T-10 | Model fabricates or misattributes speaker/entity/fact and downstream consumers treat it as ground truth | High | Evidence spans and source timestamps; confidence/uncertainty; schema validation; provenance; distinguish inference from direct observation | Ground-truth evaluation; attribution error metrics; unsupported-claim and citation checks |
| T-11 | Malicious or poisoned documents/transcripts pollute retrieval and influence future answers | High | Source trust labels; ingestion review/quarantine; tenant-scoped indexes; provenance; retrieval filters; content treated as data not instruction | Poisoning fixtures; retrieval isolation tests; citation/provenance assertions |
| T-12 | MCP/API capability exposes broader actions or data than caller is entitled to | High | Per-tool authorization; narrow scopes; explicit capability manifests; argument validation; audit every sensitive action | Tool-level authorization matrix; negative tests with low-privilege tokens |
| T-13 | Agent changes code/policy, deploys, deletes data, or incurs spend outside intended scope | Critical | Separate read/write capabilities; environment-specific credentials; deny production/deletion/spend by default; approval/policy gates; branch protection; audit and rollback | Simulated agent tool-use tests; verify denied actions; review capability grants and audit trail |
| T-14 | Dependency, model, container, or build supply-chain compromise | High | Pin and verify dependencies/images; SBOM; vulnerability scanning; provenance/signature verification; minimal base images; controlled model artifacts | CI dependency scan; SBOM generation; image scan; reproducible build checks |
| T-15 | Logs, traces, crash dumps, or evaluation fixtures retain sensitive content indefinitely | High | Structured redaction; content-free default telemetry; retention TTL; restricted access; synthetic fixtures | Canary secret/audio marker scan across logs, traces, artifacts, and test reports |
| T-16 | Cross-tenant or stale semantic cache returns a result generated under different permissions or data policy | High | Cache key includes tenant, schema, model, policy, and authorization-relevant context; invalidate on deletion/policy changes; do not cache sensitive outputs by default | Cache isolation tests; permission-change and deletion invalidation tests |

## 6. Required security invariants

1. No request may select or override its tenant identity through an untrusted payload field.
2. No retrieved content, transcript, model output, or file content may grant itself authority or change agent policy.
3. No worker may process unbounded media or invoke unbounded retries/model spend.
4. Every derived record must retain enough lineage to identify and delete its source descendants.
5. Every external provider call must be attributable to a job, policy decision, provider, model/version, and budget.
6. Every privileged agent action must be checked by a policy enforcement point outside the LLM.
7. Cross-tenant access must fail closed, including caches, vector search, object storage, exports, and MCP tools.
8. A deletion response must distinguish accepted, in-progress, completed, and backup/provider-retention-pending states.

## 7. Security test plan

The following suites are release gates when the corresponding feature exists:

- **Authorization:** tenant isolation, object-level authorization, role/scope matrix, MCP tool authorization.
- **Input and parser safety:** media fuzzing, decompression limits, MIME/signature mismatch, malformed metadata.
- **Prompt/tool safety:** spoken prompt-injection corpus, retrieved-document injection, tool argument abuse, unauthorized action attempts.
- **Reliability/integrity:** webhook replay, duplicate jobs, worker crash at every checkpoint, out-of-order events, idempotency.
- **Privacy:** log/trace redaction, provider egress allowlist, retention expiry, full deletion propagation.
- **Resource abuse:** upload quotas, concurrency caps, provider budget caps, cancellation and backpressure.
- **Supply chain:** dependency/image scanning, SBOM, artifact provenance, secret scanning.

Each test must record the threat ID, environment, fixture provenance, expected denial/behavior, observed result, and build revision.

## 8. Residual risks and open decisions

- Legal basis, recording consent/notice, data residency, and retention requirements depend on target markets and deployment model; product/legal requirements are not yet defined.
- Third-party model and speech providers may have retention, regional processing, or training terms that change; each provider requires a current review.
- Speaker diarization and voice identity are probabilistic. Do not use voice biometrics or identity claims without a separately approved purpose and risk review.
- Deletion from immutable backups and external providers may be delayed; user-facing guarantees must reflect actual contracts and tested behavior.
- No production implementation currently exists; all mitigations above are requirements until verified by code and tests.

## 9. Reassessment triggers

Revisit this model when introducing real-time streaming, public MCP access, multi-region storage, voice biometrics, customer-managed keys, new external providers, sandbox execution, or autonomous production deployment.
