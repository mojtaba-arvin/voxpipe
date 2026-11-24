# Speech telemetry runtime configuration

VoxPipe's speech observer is provider-neutral and content-free. This optional
runtime factory wires it to the OpenTelemetry SDK and OTLP/gRPC exporters.
It does not install global tracer/meter providers and does not start exporting
until the application explicitly constructs it.

## Install the optional exporter

```bash
python -m pip install -r requirements-observability.txt
```

## Configure the worker process

Set the standard OpenTelemetry OTLP environment variables in the worker's
secret/configuration system. For example:

- `OTEL_EXPORTER_OTLP_ENDPOINT`: collector endpoint (use a TLS-protected
  endpoint outside a trusted local development environment).
- `OTEL_EXPORTER_OTLP_HEADERS`: collector authentication headers, injected
  as a secret; never print this value.
- `OTEL_EXPORTER_OTLP_CERTIFICATE`: CA certificate path when using a private
  certificate authority.

Then construct the runtime once per worker process and pass its observer to
`SpeechToTextService`:

```python
from voxpipe.speech import SpeechToTextService
from voxpipe.speech.telemetry_runtime import configure_speech_otel

telemetry = configure_speech_otel(
    service_name="voxpipe-worker",
    trace_sample_ratio=0.05,
    metric_export_interval_ms=60_000,
)
speech_service = SpeechToTextService(adapter, observer=telemetry.observer)

# During the worker's normal graceful-shutdown path:
telemetry.shutdown()
```

Call `shutdown()` only after in-flight work has drained. It flushes and stops
the metric and trace providers and is idempotent. Each process owns its own SDK
providers; this factory intentionally does not mutate process-global providers.

## Resource, sampling and privacy policy

- Resource attributes are limited to `service.name` and the fixed
  `service.namespace=voxpipe`. Do not add tenant, asset, user, audio or
  transcript identifiers.
- Trace sampling is explicit and bounded to `[0, 1]`; the default is 5%.
  Choose a lower ratio under high volume and validate collector capacity.
- Metrics use the existing bounded provider/outcome/error-code dimensions.
  Never add raw exception text, request IDs, tenant IDs or asset IDs as labels.
- Use authenticated TLS for remote collectors. Endpoint, headers and
  certificate configuration are read by the upstream OTLP exporters from
  `OTEL_EXPORTER_OTLP_*` environment variables.
- If the collector is unavailable, the application should continue to rely
  on the existing best-effort observer isolation. Exporter health, queue
  saturation, dropped telemetry and shutdown flush behavior still need
  staging verification before an operational SLO can be claimed.

This is an opt-in runtime configuration, not a production deployment or proof
that a collector, dashboard, alert, or SLO is already operational.
