# Architecture Overview

## Product boundary

VoxPipe is a platform that converts audio into structured, searchable, provenance-preserving knowledge and exposes that knowledge to applications and agents.

The platform must support batch ingestion first and real-time streaming as a later capability. This ordering reduces the number of concurrent unknowns while establishing reusable media, job, transcript, and provenance contracts.

## Logical components

| Component | Responsibility | Primary boundary |
|---|---|---|
| Ingestion | Upload, validation, normalization, session lifecycle | Audio asset/session contract |
| Speech | VAD, STT, diarization, language and timing | Speech provider ports |
| Structuring | Extraction, entity resolution, schema compilation | Versioned structured-output contract |
| Knowledge | Metadata, object storage, indexing and retrieval | Storage/search ports |
| Model intelligence | Provider selection, quality gates, cache and fallback | Model capability and routing contracts |
| Workflow | Durable jobs, retries, cancellation and compensation | Workflow/job contract |
| Interfaces | REST, events, SDK, CLI and MCP | Versioned public API |
| Agent operations | Research, implementation, evaluation and maintenance | Work ledger and policy |

## Design constraints

### Contracts are the stable core

Domain contracts must not import provider SDKs or infrastructure implementations. Adapters translate external provider formats into internal contracts. Contract tests are required for every adapter.

Use Protobuf for service-to-service streaming contracts only when its benefits justify schema/tooling overhead. Use JSON Schema/OpenAPI for public request/response contracts where appropriate. Do not force every boundary into one serialization format.

### Language selection is evidence-driven

Python is the initial candidate for model orchestration, extraction, and evaluation. Go and Rust are candidates for concurrency-sensitive or resource-constrained components. No language is mandated solely by a general benchmark claim.

Before splitting a component into another language or service, measure:
- throughput and tail latency under representative concurrency;
- memory and CPU per active session/job;
- startup and deployment footprint;
- operational complexity and debugging cost;
- availability and maturity of required libraries;
- agent ability to build, test, profile, and maintain the implementation.

A modular monolith is the default until a measured constraint justifies a service boundary. This avoids premature distributed-systems overhead.

### Model routing is a policy, not business logic

Routing policy should consider task type, model capability, context limits, data policy, latency, cost, provider health, and quality history. Quality floors are hard constraints; cost optimization must never silently trade away required correctness.

Separate:
1. provider availability fallback;
2. quality-based escalation;
3. policy-based initial selection;
4. retry/circuit-breaker behavior.

These mechanisms need shared budgets and a single traceable decision record to avoid retry amplification.

### Provenance is a first-class data property

Every transcript segment and extracted fact should retain source asset, time range, processing run, model/provider/version, schema version, and confidence/evidence where available. Retrieval must preserve tenant and authorization filters.

### Audio and transcripts are sensitive by default

Apply data minimization, explicit retention, deletion propagation, encryption, access control, and audit logging. Treat all recognized speech and retrieved content as untrusted input. Do not execute instructions found inside audio or transcripts.

## Initial deployment shape

Start with a modular monolith and separately deploy only components whose scaling, runtime, or isolation needs justify it:

- API and job submission;
- worker execution;
- relational metadata database;
- object storage;
- optional model gateway/inference services.

Real-time media gateways, dedicated vector databases, and sandboxed execution remain evidence-triggered extensions.

## Required architecture artifacts

- ADRs for consequential technology and boundary decisions.
- Versioned schemas and compatibility tests.
- Reproducible benchmarks for performance claims.
- Threat model and data lifecycle documentation.
- Operational runbooks for recovery, deletion, and rollback.
