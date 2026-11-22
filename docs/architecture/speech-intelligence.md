# Speech Intelligence Contracts

## Scope

VP-0021 establishes the provider-neutral boundary and canonical transcript v1. It does not select or call a hosted STT provider, download model weights, or incur inference spend. Provider selection remains a measured follow-up based on Persian/multilingual accuracy, diarization, word timing, latency, privacy, operational complexity, and total cost.

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


## Diarization adapter contract

`DiarizationRequest` standardizes normalized mono 16 kHz samples, the source-audio SHA-256 and ordered VAD speech regions. The ECAPA adapter exposes `DiarizationPort.diarize_request`; speaker labels are anonymous and scoped to the asset. `SpeakerTurn` validates positive time spans and bounded confidence, and result objects declare whether overlap is supported.

The current ECAPA sliding-window baseline explicitly reports `overlap_supported=false`; it must not be represented as overlap-aware diarization. The current Neyshekar experiment is a controlled concatenation of read speech, not naturally recorded multi-speaker conversation. Production DER/JER acceptance still requires a consented natural-conversation corpus with speaker-turn and overlap annotations.
