# ADR-0023: Reliable, observable and provider-neutral STT execution

- **Status:** Accepted for architecture and local reliability primitives; hosted-provider rollout remains gated.
- **Date:** 2026-11-23
- **Work items:** VP-0022, VP-0032, VP-0033, VP-0034, VP-0035

## Context

VoxPipe must support both self-hosted speech recognition and optional
third-party STT services. Vendor APIs differ in authentication, upload format,
timeouts, asynchronous job semantics, idempotency, timestamps, language
coverage, data residency, retention and pricing. These differences must not
leak into transcript persistence or downstream intelligence.

## Decision

Keep `SpeechToTextPort` and the canonical transcript schema as the only
application boundary. Implement each vendor as a separate adapter and keep
Faster-Whisper as a fully independent local adapter.

A hosted adapter is not enabled merely because credentials exist. Operators
must explicitly allowlist provider/model/region, data egress, retention policy,
features, audio limits and spend ceilings using verified capability metadata.

Retries are owned by durable workflow orchestration, not hidden in adapters.
A retry after an ambiguous timeout can duplicate a billable transcription; use
provider idempotency keys and request reconciliation where the vendor supports
them. Otherwise stop and require safe reconciliation rather than assume the
first request failed.

The service emits privacy-safe lifecycle events. Provider-scoped circuit
breaking protects against repeated transient outages. The current breaker is
process-local and is not a distributed availability guarantee.

## Consequences

- A vendor outage cannot corrupt the canonical transcript model.
- Local inference remains available without hosted credentials or network.
- Vendor SDKs stay isolated in optional adapter dependencies.
- Operators can compare local and hosted quality, latency, availability and
  effective cost without changing downstream APIs.
- Production use still requires a vendor-specific contract, data-processing
  approval, shared breaker/retry state, exporter, alerting and failover tests.
