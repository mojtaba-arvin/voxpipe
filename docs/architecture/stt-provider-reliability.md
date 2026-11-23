# Speech-to-Text Provider Reliability and Observability

## Architectural decision

VoxPipe supports two first-class execution modes behind the same canonical
`SpeechToTextPort` and transcript contract:

1. **Self-hosted inference** (currently Faster-Whisper): audio remains inside
   the operator-controlled worker boundary.
2. **Third-party inference**: a provider-specific adapter is optional and must
   normalize vendor responses into the same immutable, validated transcript.

Neither mode is the system of record. The audio asset, transcript revision,
provider/model provenance and workflow journal remain VoxPipe-owned. Provider
selection is an operational policy, never a schema dependency.

## Reliability boundary

Every hosted adapter must implement the following controls before production
allowlisting:

- Explicit connect, read and total deadlines; bounded response size and audio
  upload size; bounded concurrency and per-provider queue capacity.
- Authentication from a secret manager/environment injection; never place
  credentials in URLs, logs, exceptions or persisted configuration.
- TLS verification, endpoint allowlisting and explicit region/data-egress
  policy. No arbitrary endpoint from user input.
- Stable error mapping into the canonical error codes; distinguish invalid
  input, unsupported feature/language, authentication, rate limit, provider
  outage, timeout and malformed response.
- Provider/model/version and source audio SHA-256 provenance on every result.
- Request correlation and provider request ID where available, sanitized before
  persistence. Never store raw request/response bodies by default.
- Vendor idempotency support must be verified before retries are enabled.
  Without it, do not blindly retry billable submissions after ambiguous
  timeouts; reconcile by provider request ID or leave the operation for
  explicit workflow recovery.
- Retry policy belongs to the durable workflow layer, not inside an HTTP
  adapter. Retry only retryable error classes, honor Retry-After, use bounded
  exponential backoff with jitter, and enforce an overall deadline and spend
  ceiling. Reuse the same idempotency key only when the provider guarantees it.
- Circuit breakers use the existing `ProviderCircuitBreaker` shared by the
  adaptive router and guarded STT adapter. It is process-local; multi-worker
  deployments need shared durable coordination before production.
- Failover is policy-driven and must not silently change quality, residency,
  retention, language, timestamp or diarization guarantees. A fallback to a
  hosted provider requires explicit egress approval.

## Observability contract

`SpeechToTextService` emits content-free lifecycle events:

- `started`
- `succeeded`
- `failed`

Events include an opaque operation ID, bounded provider identifier, duration,
stable error code and retryability. They deliberately exclude audio, transcript,
asset/tenant IDs, secrets, local paths, vendor payloads and user-supplied text.
Observer failures are isolated from the transcription result.

Production instrumentation should map these events to OpenTelemetry metrics and
traces with bounded labels. Recommended measurements:

- request count by provider/model/operation outcome
- duration histogram and p50/p95/p99
- retry count and retry exhaustion
- provider timeout, rate-limit and authentication failures
- circuit state transitions and open duration
- in-flight requests, queue age and saturation
- audio duration/bytes buckets (not raw identifiers)
- estimated and reconciled cost by provider/model
- fallback count and reason
- transcript validation rejection count

Never use tenant ID, asset ID, transcript text, API key, provider request ID or
arbitrary error message as metric labels. Correlation identifiers belong in
traces/log fields with access controls, not metric dimensions.

## Self-hosted inference requirements

Faster-Whisper remains an independent first-class adapter. Production rollout
must pin package and model revisions, verify model artifact digests, prewarm
models before accepting traffic, bound CPU/RAM/VRAM and audio bytes, isolate
workers, monitor model-load and inference latency, and clean temporary audio
files on every exit path. Local inference failures must not be mislabeled as
invalid user audio when the model/runtime is unavailable.

## Current implementation boundary

Implemented:
- Provider-neutral adapter interface and canonical transcript validation.
- Faster-Whisper local adapter.
- Content-free lifecycle observer and best-effort telemetry isolation.\n- OpenTelemetry bridge for operation counters, duration histograms and correlated traces; meter/tracer/exporter configuration remains injected by the host application. Optional packages are listed in `requirements-observability.txt`.
- Guarded adapter reusing the same fenced process-local circuit breaker as
  adaptive routing; no duplicate breaker state machine.
- Tests for telemetry, observer isolation and circuit integration.

Not yet production-accepted:
- A vendor-specific hosted adapter (requires operator-selected vendor, region,
  DPA/retention review, explicit data-egress approval and pinned API contract).
- Shared/distributed circuit state and durable retry/idempotency reconciliation.
- OpenTelemetry exporter, dashboards, alerts and staging SLO verification.
- Production workload, load, timeout and failover testing.

No hosted provider is enabled by default. No customer audio is transferred.
