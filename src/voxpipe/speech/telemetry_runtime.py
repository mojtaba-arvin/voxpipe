"""Optional OTLP SDK bootstrap for VoxPipe speech telemetry.

This module is deliberately opt-in: importing VoxPipe does not create global
OpenTelemetry providers, open network connections, or start exporter threads.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from voxpipe.speech.observability import OpenTelemetrySpeechObserver


@dataclass(slots=True)
class SpeechTelemetryRuntime:
    """Own the SDK providers and observer for one application/worker process."""

    observer: OpenTelemetrySpeechObserver
    _meter_provider: Any
    _tracer_provider: Any
    _closed: bool = False

    def shutdown(self) -> None:
        """Flush and stop both exporters; safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        failures: list[Exception] = []
        for provider in (self._meter_provider, self._tracer_provider):
            try:
                provider.shutdown()
            except Exception as exc:  # cleanup must still reach the other provider
                failures.append(exc)
        if failures:
            raise RuntimeError("one or more OpenTelemetry providers failed to shut down") from failures[0]


def configure_speech_otel(
    *,
    service_name: str,
    trace_sample_ratio: float = 0.05,
    metric_export_interval_ms: int = 60_000,
) -> SpeechTelemetryRuntime:
    """Create an OTLP-backed observer without mutating global SDK providers.

    OTLP exporter endpoints, TLS certificates and headers are read from the
    standard OTEL_EXPORTER_OTLP_* environment variables. Install the optional
    requirements-observability.txt set before calling this function.
    """
    if not isinstance(service_name, str) or not service_name.strip() or len(service_name) > 255:
        raise ValueError("service_name must be a non-empty string of at most 255 characters")
    if not isinstance(trace_sample_ratio, (int, float)) or isinstance(trace_sample_ratio, bool):
        raise ValueError("trace_sample_ratio must be a finite number in [0, 1]")
    try:
        sample_ratio = float(trace_sample_ratio)
    except (OverflowError, ValueError) as exc:
        raise ValueError("trace_sample_ratio must be a finite number in [0, 1]") from exc
    if not math.isfinite(sample_ratio) or not 0.0 <= sample_ratio <= 1.0:
        raise ValueError("trace_sample_ratio must be a finite number in [0, 1]")
    if (
        not isinstance(metric_export_interval_ms, int)
        or isinstance(metric_export_interval_ms, bool)
        or not 1_000 <= metric_export_interval_ms <= 300_000
    ):
        raise ValueError("metric_export_interval_ms must be an integer in [1000, 300000]")

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError as exc:
        raise RuntimeError(
            "OTLP speech telemetry requires the optional dependencies in "
            "requirements-observability.txt"
        ) from exc

    resource = Resource.create({
        "service.name": service_name.strip(),
        "service.namespace": "voxpipe",
    })
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(sample_ratio),
    )
    meter_provider = None
    try:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(),
            export_interval_millis=metric_export_interval_ms,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        observer = OpenTelemetrySpeechObserver(
            meter=meter_provider.get_meter("voxpipe.speech"),
            tracer=tracer_provider.get_tracer("voxpipe.speech"),
        )
    except Exception:
        if meter_provider is not None:
            try:
                meter_provider.shutdown()
            except Exception:
                pass
        try:
            tracer_provider.shutdown()
        except Exception:
            pass
        raise

    return SpeechTelemetryRuntime(
        observer=observer,
        _meter_provider=meter_provider,
        _tracer_provider=tracer_provider,
    )
