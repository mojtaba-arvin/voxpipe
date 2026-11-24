# ADR-0035: Add an opt-in Deepgram Nova-3 hosted STT adapter

- **Status:** Accepted as an optional adapter; hosted activation remains disabled.
- **Date:** 2026-11-24
- **Work item:** VP-0022

## Context

VoxPipe already exposes a provider-neutral `SpeechToTextPort`, canonical transcript contracts, durable transcription workflows and an offline Faster-Whisper adapter. A hosted provider is useful for provider comparison and capabilities not supplied by the local adapter, but must not become an implicit default or bypass privacy, cost, retry and tenant controls.

Deepgram's current Nova-3 multilingual API documents Persian (`fa`) among 55 languages, supports language hints and code-switching, and exposes word-level language metadata. Nova batch transcription supports diarization using `diarize_model=latest`. Sources: [Nova-3 language prompting](https://developers.deepgram.com/docs/nova-3-language-prompting), [model/language overview](https://developers.deepgram.com/docs/models-languages-overview), [speaker diarization](https://developers.deepgram.com/docs/diarization).

## Decision

Implement `DeepgramNova3Adapter` behind the existing `SpeechToTextPort` and canonical transcript contract.

- Use the HTTPS batch `/v1/listen` endpoint with `model=nova-3&language=multi` and `utterances=true`.
- Reduce a BCP-47 request hint to its base language code and pass it as `language_hint`; leave language auto-detection enabled when no hint is supplied.
- Enable batch diarization only when explicitly requested, using `diarize_model=latest`.
- Convert provider utterances, words, language and speaker labels into canonical VoxPipe records. Derive opaque, asset-scoped speaker UUIDs; do not expose provider SDK types.
- Use the Python standard library for HTTPS transport; no provider SDK or new runtime dependency is required.
- Require `DEEPGRAM_API_KEY` and explicit provider/model allowlisting at runtime. The adapter is not selected by default and must not be auto-routed.
- Bound audio bytes, response bytes and request time. Map HTTP errors to stable, redacted VoxPipe error codes.
- Do not forward VoxPipe's workflow idempotency key: the batch endpoint does not document request idempotency. An ambiguous network timeout is therefore non-retryable to avoid duplicate billable inference. A future reconciliation mechanism requires vendor-supported request lookup or idempotency evidence.

## Cost, privacy and rollout gate

Data egress has been approved for this provider, but the current test budget is **USD 0** and no API key is provisioned. Consequently:

- CI uses an injected fake HTTP transport and performs no network call, audio upload, API-key lookup, or paid inference.
- The adapter must fail closed when no key is configured.
- Live smoke tests, hosted benchmark runs and production allowlisting remain disabled until an API key is provisioned and a positive spend ceiling is explicitly approved.
- A zero-cost routing policy must continue to exclude this hosted model; provider availability is not evidence of measured quality.

## Consequences

Positive:
- A real hosted integration path exists without changing the speech port or workflow handler.
- Persian multilingual transcription and optional speaker diarization are available behind explicit opt-in.
- Unit tests verify request construction, response normalization, error mapping, timeouts, response limits and no-key behavior without external calls.

Limitations:
- No live API call or vendor response has been verified in this environment.
- Provider-side idempotency and safe replay of ambiguous submissions are not claimed.
- Deepgram timestamps, language labels and diarization quality have not been measured on VoxPipe's approved evaluation corpus.
- A hosted adapter does not close Persian human-gold-set or natural-conversation corpus requirements.

## Rollback

Remove the Deepgram adapter from the runtime allowlist or unset `DEEPGRAM_API_KEY`. The local Faster-Whisper adapter, provider-neutral port and canonical transcript contract remain unchanged.
