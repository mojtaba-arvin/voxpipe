# Speech resilience runtime configuration

The Python speech runtime exposes an explicit composition helper:
voxpipe.speech.runtime.build_speech_resilience_runtime(adapter). It returns
the guarded speech_port, the selected breaker and the selected backend name.
`build_local_speech_worker` now invokes this factory and injects the guarded
port into `SpeechTranscriptionWorkflowHandler`; the breaker therefore protects
the actual worker transcription path rather than remaining an unused helper.
The worker owns the guarded port for its process lifetime.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| VOXPIPE_CIRCUIT_BREAKER_BACKEND | process | process or postgres |
| VOXPIPE_DATABASE_DSN | unset | Required only for the PostgreSQL backend |
| VOXPIPE_CIRCUIT_BREAKER_FAILURE_THRESHOLD | 3 | Consecutive availability failures, 1–100 |
| VOXPIPE_CIRCUIT_BREAKER_COOLDOWN_SECONDS | 30 | Cooldown, 1–3600 seconds |

The process-local backend is retained as the compatibility default. It is now
used by the supervised worker unless explicitly overridden. To share provider
availability across workers, set `VOXPIPE_CIRCUIT_BREAKER_BACKEND=postgres` and
provide `VOXPIPE_DATABASE_DSN` with the dedicated application-role database DSN. The PostgreSQL adapter uses the
existing migration 0010 table and requires its documented grants before traffic
is admitted.

## Failure policy

Selecting PostgreSQL is an explicit deployment decision. Missing DSN or an
unknown backend fails during composition; the factory never silently falls
back to process-local state. At request time, PostgreSQL connection/query
errors propagate from the breaker and fail the guarded STT call closed: the
provider adapter is not invoked without an admission permit. This prioritizes
avoiding uncoordinated duplicate provider traffic over availability during a
coordination-store outage. Recovery is automatic on a later request after the
database is healthy; no local shadow state is created.

A PostgreSQL half-open probe owns a five-minute lease. If its worker exits
without reporting success/failure or releasing the probe, another worker may
acquire a fresh fenced probe after that lease expires. Configure every provider
request's hard timeout below this lease; the old permit cannot clear or reopen
a circuit after a replacement probe has been issued. Migration `0011` adds the
expiry column and preserves any in-flight probe for at most five minutes during
upgrade.

The factory does not enable hosted inference, alter the router allowlist, add
implicit retries, or claim production activation. The default remains local
until an operator deliberately configures PostgreSQL in each worker's
environment. A deployment must verify migration 0010, least-privilege grants,
database availability and alerting before enabling it.

## Example

    from voxpipe.speech.runtime import build_speech_resilience_runtime
    from voxpipe.workflows.speech import SpeechTranscriptionWorkflowHandler

    resilience = build_speech_resilience_runtime(stt_adapter)
    handler = SpeechTranscriptionWorkflowHandler(
        repository=transcript_repository,
        object_store=object_store,
        adapter=resilience.speech_port,
    )
    # Keep resilience alive for the worker process lifetime.
