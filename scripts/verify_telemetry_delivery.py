"""Emit one synthetic privacy-safe OTLP metric and verify Prometheus ingestion."""
from __future__ import annotations

import json
import os
import time
from urllib.parse import quote
from urllib.request import urlopen

from voxpipe.speech.observability import SpeechOperationEvent
from voxpipe.speech.telemetry_runtime import configure_speech_otel


def query_prometheus(query: str) -> dict:
    url = "http://127.0.0.1:9090/api/v1/query?query=" + quote(query)
    with urlopen(url, timeout=3) as response:
        payload = json.loads(response.read())
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus query failed")
    return payload["data"]


def main() -> None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint != "http://127.0.0.1:4317":
        raise SystemExit("CI telemetry endpoint must target the disposable local Collector")

    runtime = configure_speech_otel(
        service_name="voxpipe-ci-telemetry",
        trace_sample_ratio=0.0,
        metric_export_interval_ms=1_000,
    )
    runtime.observer.record(SpeechOperationEvent(
        operation_id="ci-synthetic-event",
        event="failed",
        provider="ci-fake-provider",
        duration_ms=123,
        error_code="CI_SYNTHETIC_FAILURE",
        retryable=True,
    ))
    runtime.shutdown()

    query = 'voxpipe_stt_operations_total{outcome="failed",provider="ci-fake-provider"}'
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        result = query_prometheus(query).get("result", [])
        if result:
            labels = result[0].get("metric", {})
            forbidden = {
                "tenant_id", "asset_id", "transcript_id", "audio", "transcript",
                "filename", "request_id", "user_id",
            }
            if forbidden.intersection(labels):
                raise SystemExit("Prometheus metric contains a prohibited high-cardinality label")
            if labels.get("error_code") != "CI_SYNTHETIC_FAILURE":
                raise SystemExit("Prometheus metric lost the bounded error_code label")
            if labels.get("retryable") not in {"true", "1"}:
                raise SystemExit("Prometheus metric lost the bounded retryable label")
            print("Prometheus ingested the synthetic failed-operation metric with bounded labels.")
            return
        time.sleep(2)
    raise SystemExit("Prometheus did not ingest the synthetic OTLP metric before timeout")


if __name__ == "__main__":
    main()
