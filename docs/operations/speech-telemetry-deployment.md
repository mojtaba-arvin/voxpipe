# Speech telemetry Collector and alert rules

This directory provides a starter, self-hosted telemetry path for the opt-in
speech SDK. It is configuration, not evidence of a deployed Collector,
Prometheus server, trace backend, alert receiver, or staging SLO.

## Topology

- Workers send OTLP/gRPC (4317) or OTLP/HTTP (4318) to the Collector on a
  private service network.
- The Collector exports metrics on 8889 for Prometheus to scrape.
- The Collector forwards traces to the configured OTLP/HTTP trace backend.
- Prometheus evaluates the rules in `rules/voxpipe-speech.yml`.

Do not publish 4317, 4318, or 8889 directly to the public internet. Put
authentication and TLS at the service-network boundary or gateway. Configure
the trace backend endpoint through the secret/configuration system as
`VOXPIPE_TRACE_BACKEND_OTLP_HTTP_ENDPOINT`; use an HTTPS endpoint. Do not put
credentials in this repository.

## Start-up contract

Use a pinned, reviewed OpenTelemetry Collector Contrib image and Prometheus
image in the deployment manifest. Mount:

- `otel-collector.yaml` as the Collector configuration;
- `prometheus.yml` as Prometheus configuration;
- `rules/` at `/etc/prometheus/rules/`.

The Collector config uses the `memory_limiter`, `batch`, `prometheus`, and
`otlphttp` components. Select a Collector distribution that includes all
four components. Configure the VoxPipe worker's standard
`OTEL_EXPORTER_OTLP_ENDPOINT` to the private Collector endpoint. For traces,
set `VOXPIPE_TRACE_BACKEND_OTLP_HTTP_ENDPOINT` in the Collector environment.
Metrics are exposed only on the Collector's internal scrape endpoint.

## Alert semantics and tuning

- `VoxPipeSpeechTelemetryTargetDown`: Prometheus cannot scrape the Collector.
- `VoxPipeSpeechFailureRatioHigh`: failed / (succeeded + failed) exceeds 5%
  for 10 minutes. Started events are intentionally excluded from the
  denominator. Low-volume behavior must be checked before paging.
- `VoxPipeSpeechLatencyP95High`: speech operation p95 exceeds 10 seconds for
  10 minutes. This is a starter trigger, not a contractual latency SLO.

The latency metric name follows the OpenTelemetry Prometheus exporter unit
normalization for the existing `voxpipe.stt.duration` histogram with unit
`ms`. Confirm the emitted series names against the selected Collector
version before enabling paging. The rules intentionally do not introduce
tenant, asset, user, transcript, audio, request, or raw-exception labels.

## Validation before deployment

Run Prometheus' `promtool check config` against `prometheus.yml` and
`promtool check rules` against `rules/voxpipe-speech.yml`. Validate the
Collector config with the exact pinned Collector image using its `validate`
command. Then perform a staging smoke test that emits one success and one
failure event, confirms metrics reach Prometheus, confirms traces reach the
configured backend, and exercises Collector restart and downstream outage.

Before paging is enabled, tune thresholds using representative staging
traffic, define an owner and notification route, and add queue-age, workflow
success, upload acceptance, and deletion-completion instrumentation. Those
signals are not present in the speech observer and are deliberately not
fabricated here.

## Runbook: target down or telemetry absent

1. Check Collector process health and its startup/configuration logs.
2. Check private-network reachability from workers and Prometheus.
3. Check Prometheus target health at `/targets`.
4. Check Collector memory-limiter and exporter queue/drop telemetry.
5. Restore Collector/backend availability; do not retry transcription merely
   because telemetry delivery failed.
6. Confirm a fresh synthetic success/failure pair appears in Prometheus and
   the trace backend.

Speech telemetry is best-effort. Application work must not depend on an
available telemetry backend. Treat exporter queue saturation, dropped
telemetry, and shutdown flush behavior as staging acceptance gates.
