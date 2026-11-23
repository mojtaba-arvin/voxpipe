# Speech Intelligence Contracts

## Scope

VP-0021 establishes the provider-neutral boundary and canonical transcript v1. VoxPipe supports self-hosted inference and optional third-party STT adapters behind the same contract. No hosted provider is enabled by default; selection requires measured Persian/multilingual accuracy, diarization, word timing, latency, privacy, residency, operational complexity and total cost.

## Canonical transcript

`contracts/schemas/transcript.v1.schema.json` is the versioned wire contract. A transcript references the immutable `asset_id`, declares a BCP-47 language tag, and contains ordered segments, optional word-level timing, optional speaker attribution, confidence values, and provenance. Provenance records provider/model identifiers, the source audio SHA-256, and generation time. Tenant identity is deliberately absent from the payload; persistence and access remain tenant-scoped by authenticated context and PostgreSQL RLS.

The JSON Schema covers structural constraints. `voxpipe.speech.validate_transcript` adds cross-field invariants that JSON Schema cannot express: segment ordering and duration bounds, word timing inside segments, speaker reference integrity, unique IDs, confidence ranges, timezone-aware timestamps, and source-asset binding.

## Adapter boundary

`SpeechToTextPort` accepts a `TranscriptionRequest` containing an already-extracted readable audio stream, content type, optional language hint, and explicit word-timing/diarization capabilities. It does not accept filesystem paths, storage keys, tenant IDs, provider SDK request types, or secrets.

Adapters return the canonical `Transcript` or raise `TranscriptionError` with a stable error code, provider identifier, retryability, and optional retry-after value. Raw provider messages and response payloads must not cross this boundary or be persisted as error details.

`SpeechToTextService` validates adapter output against the requested asset and known audio duration before returning it to downstream stages. The initial tests use a deterministic in-memory fake; a real provider adapter must be added only after the evaluation and privacy gates are defined.

## Deferred work

- Provider-specific STT adapters and benchmark harness.
- VAD, language identification, diarization, and audio enhancement.
- Consent/provenance-managed multilingual evaluation corpus.
- Transcript persistence, versioning, retention/deletion propagation, and searchable indexing.


## VAD adapter contract

`VADRequest` is the provider-neutral boundary for normalized mono float samples, sample rate and source-audio SHA-256. Both Silero and WebRTC adapters implement `VADPort.detect_request`; WebRTC PCM16 framing/conversion is private to its adapter. The older low-level adapter methods remain available for the pinned benchmark. Request validation rejects non-finite/out-of-range samples, unsupported sample rates and malformed provenance before inference.

The adapters still have different model capabilities (Silero is limited to 8/16 kHz, while WebRTC supports 8/16/32/48 kHz). The direct Silero API routes through normalized request validation; Silero options reject booleans/non-finite thresholds and non-integer duration settings; provider timestamps must be integer sample boundaries. WebRTC validates the injected detector interface and source digest. `VADResult` and the evaluator enforce ordered, non-overlapping regions within the source duration. These are contract/invariant guarantees, not model-accuracy claims. FLEURS utterance envelopes remain proxy labels and cannot close the frame-level Persian VAD gate.


## Hosted-provider reliability and observability

A third-party adapter must isolate vendor SDKs and normalize responses into the canonical transcript contract. It owns authentication, TLS, endpoint allowlisting, upload/response limits, explicit deadlines, bounded concurrency, region selection and stable error mapping. Credentials and raw vendor payloads never enter logs or public errors.

Hosted providers require explicit operator allowlisting, data-egress and retention approval, verified capabilities and a spend ceiling. Retries are owned by durable workflow orchestration and must use vendor idempotency/reconciliation after ambiguous timeouts; blind retries can create duplicate billable work. A guarded adapter can reuse the same fenced process-local `ProviderCircuitBreaker` used by adaptive routing. Shared multi-process breaker state remains a production gate.

`SpeechToTextService` emits content-free started/succeeded/failed lifecycle events through an optional observer. Telemetry excludes audio, transcript text, asset/tenant identifiers, credentials, local paths and raw provider payloads. Observer failures cannot fail transcription.

See [STT provider reliability](stt-provider-reliability.md) and [ADR-0023](../decisions/ADR-0023-stt-provider-reliability.md) for the full reliability, privacy, failover and observability contract.
