# Speech Intelligence Contracts

## Scope

VP-0021 establishes the provider-neutral boundary and canonical transcript v1.
VoxPipe supports self-hosted inference and optional third-party STT adapters
behind the same application contract. No hosted provider is enabled by default.
Provider selection remains a measured decision based on Persian/multilingual
accuracy, timestamps, diarization, latency, privacy, operational complexity,
data residency and total cost.

## Canonical transcript

`contracts/schemas/transcript.v1.schema.json` is the versioned wire contract.
A transcript references the immutable `asset_id`, declares a BCP-47 language
tag, and contains ordered segments, optional word-level timing, optional
speaker attribution, confidence values and provenance. Provenance records
provider/model identifiers, source audio SHA-256 and generation time. Tenant
identity is deliberately absent from the payload; persistence and access remain
tenant-scoped by authenticated context and PostgreSQL RLS.

The JSON Schema covers structural constraints.
`voxpipe.speech.validate_transcript` adds cross-field invariants: segment
ordering and duration bounds, word timing inside segments, speaker reference
integrity, unique IDs, confidence ranges, timezone-aware timestamps and
source-asset binding.

## Adapter boundary

`SpeechToTextPort` accepts a `TranscriptionRequest` containing an
already-extracted readable audio stream, content type, optional language hint
and explicit word-timing/diarization capabilities. It does not accept tenant
IDs, provider SDK request types or secrets. Each provider-specific adapter
owns its authentication, transport, upload protocol, deadlines, response-size
limits and vendor-to-canonical error mapping.

Adapters return the canonical `Transcript` or raise `TranscriptionError`
with a stable error code, provider identifier, retryability and optional
retry-after value. Raw provider messages and response payloads must not cross
this boundary or be persisted as error details.

`SpeechToTextService` validates adapter output against the requested asset,
audio digest and known duration. It emits content-free lifecycle events through
an optional observer; observer failures do not affect transcription. Events
exclude audio, transcript, asset/tenant IDs, credentials, local paths and raw
vendor payloads.

## Reliability and provider choice

- Faster-Whisper is the independent local adapter; it does not require hosted
  credentials or transfer audio to a vendor.
- Third-party adapters are opt-in and require explicit operator allowlisting,
  verified capabilities, region/retention review, data-egress approval and a
  spend ceiling.
- Provider-scoped circuit breaking is available as a process-local guard.
  Multi-worker deployments require shared breaker state before production use.
- Retries belong to durable workflow orchestration. A billable submission must
  not be retried after an ambiguous timeout unless provider idempotency and
  request reconciliation are verified.
- Failover must preserve declared quality, language, timestamp, diarization,
  privacy and residency requirements; no silent downgrade is allowed.

See [STT provider reliability](stt-provider-reliability.md) and
[ADR-0023](../decisions/ADR-0023-stt-provider-reliability.md).

## Remaining work

- Implement and evaluate a vendor-specific adapter after operator selection and
  explicit data-egress approval.
- Move breaker state to shared durable storage and connect retries to workflow
  idempotency/reconciliation.
- Deploy OpenTelemetry exporters, dashboards, alerts and staging SLO checks.
- Complete VAD, language identification, diarization and consented corpus work.
- Continue transcript persistence, revisioning, retention/deletion propagation
  and searchable indexing under their own acceptance criteria.
