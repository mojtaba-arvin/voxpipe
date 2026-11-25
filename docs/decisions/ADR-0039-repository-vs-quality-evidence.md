# ADR-0039: Separate repository completion from external speech-quality evidence

- **Status:** Accepted
- **Date:** 2026-11-25
- **Supersedes:** No prior architecture decision; refines repository milestone acceptance under ADR-0038.

## Context

Six work items (VP-0023, VP-0024, VP-0025, VP-0026, VP-0028 and VP-0031) had implemented provider-neutral contracts, adapters, validators, evaluators, persistence, governance tooling and deterministic CI coverage, but remained marked `in_progress` because additional evidence depended on third-party annotators, privately consented natural recordings, human adjudication, or a human-judged relevance set.

These are legitimate requirements before claiming real-world model quality. They are not reproducible repository engineering tasks that an agent can complete without the data owners and qualified reviewers. Synthetic labels or model-generated annotations must never be substituted and represented as gold truth.

## Decision

1. Split each work item's completion into **repository implementation** and **external product-quality evidence**.
2. Mark the repository work item Done when its contracts, implementation, deterministic safety tests, evaluator, available licensed public-proxy results and limitations are present and CI-verified.
3. Keep external evaluation explicitly non-gating for repository milestones. It may be pursued later when lawful, consented, representative data and qualified reviewers are available.
4. Preserve exact limitations: FLEURS utterance envelopes are not frame-level VAD labels; controlled read-speech diarization is not natural overlapping conversation; FLEURS LID does not prove code-switch/noisy-field performance; synthetic extraction smoke does not establish semantic accuracy; retrieval evaluator tests do not establish relevance without human judgments.
5. Never claim individual consent from a dataset reuse license, never fabricate annotations, and never publish customer audio or identifying transcripts as public fixtures.
6. Deepgram remains disabled without a key and explicit egress authorization; paid test spend remains USD 0. Production model selection, live-provider quality/cost, customer traffic, staging SLOs and production operations are not inferred from CI.
7. Keep issues #32, #47 and #48 as optional external quality-program follow-ups. Their open state does not block repository milestone completion.

## Applied work items

- **VP-0023:** VAD contracts/adapters, validation, evaluator and FLEURS proxy benchmark are complete; frame-labelled Persian VAD remains unproven.
- **VP-0024:** ECAPA contract/adapter, explicit overlap capability, DER/JER evaluator and controlled Neyshekar benchmark are complete; natural conversation quality remains unproven.
- **VP-0025:** inventory-aware LID and calibrated FLEURS abstention evaluation are complete; code-switch/noisy-field performance remains unproven.
- **VP-0026:** corpus manifest schema/validator, public/customer separation, provenance policy and annotation disagreement diagnostics are complete; no private consented corpus or third-party adjudication is claimed.
- **VP-0028:** typed extraction, evidence validation, prompt-injection boundary, tenant-isolated persistence and synthetic integration smoke are complete; no human-reviewed semantic-quality score is claimed.
- **VP-0031:** lexical/pgvector adapters, hybrid fusion, tenant/provenance/deletion controls and evaluator tests are complete; no human-judged relevance score or production embedding choice is claimed.

## Consequences

All 37 repository work items can be closed without inventing external evidence or asking the maintainer to perform manual annotation/benchmark work. Future external datasets can improve product-quality claims without reopening completed repository implementation work. The README, roadmap and machine-readable ledger must preserve the distinction and must not describe VoxPipe as production-ready.
