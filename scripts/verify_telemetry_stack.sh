#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NETWORK="voxpipe-telemetry-ci-$RANDOM"
COLLECTOR="voxpipe-otel-ci-$RANDOM"
PROMETHEUS="voxpipe-prom-ci-$RANDOM"
TMP_DIR="$(mktemp -d)"
cleanup() {
  docker rm -f "$PROMETHEUS" "$COLLECTOR" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# Validate both configurations with the exact pinned runtime images.
docker run --rm \
  -e VOXPIPE_TRACE_BACKEND_OTLP_HTTP_ENDPOINT=http://127.0.0.1:4318 \
  -v "$ROOT/ops/observability/otel-collector.yaml:/etc/otelcol-contrib/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.139.0 \
  validate --config=/etc/otelcol-contrib/config.yaml

docker run --rm \
  -v "$ROOT/ops/observability:/etc/prometheus:ro" \
  prom/prometheus:v3.7.0 \
  promtool check config /etc/prometheus/prometheus.yml

docker run --rm \
  -v "$ROOT/ops/observability:/etc/prometheus:ro" \
  prom/prometheus:v3.7.0 \
  promtool check rules /etc/prometheus/rules/voxpipe-speech.yml

# Use a faster scrape interval only in the disposable CI copy.
sed 's/scrape_interval: 15s/scrape_interval: 2s/g; s/evaluation_interval: 15s/evaluation_interval: 2s/g' \
  "$ROOT/ops/observability/prometheus.yml" > "$TMP_DIR/prometheus.yml"

docker network create "$NETWORK" >/dev/null

docker run -d --name "$COLLECTOR" --network "$NETWORK" --network-alias otel-collector \
  -p 4317:4317 -p 8889:8889 \
  -e VOXPIPE_TRACE_BACKEND_OTLP_HTTP_ENDPOINT=http://127.0.0.1:4318 \
  -v "$ROOT/ops/observability/otel-collector.yaml:/etc/otelcol-contrib/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.139.0 \
  --config=/etc/otelcol-contrib/config.yaml >/dev/null

docker run -d --name "$PROMETHEUS" --network "$NETWORK" \
  -p 9090:9090 \
  -v "$TMP_DIR/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$ROOT/ops/observability/rules:/etc/prometheus/rules:ro" \
  prom/prometheus:v3.7.0 \
  --config.file=/etc/prometheus/prometheus.yml >/dev/null

for attempt in $(seq 1 45); do
  if curl --fail --silent http://127.0.0.1:9090/-/ready >/dev/null; then
    break
  fi
  if [[ "$attempt" == 45 ]]; then
    docker logs "$COLLECTOR" || true
    docker logs "$PROMETHEUS" || true
    echo "Prometheus did not become ready" >&2
    exit 1
  fi
  sleep 2
done

OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317 \
PYTHONPATH=src python scripts/verify_telemetry_delivery.py

echo "Collector configuration, Prometheus rules, OTLP delivery and scrape verification passed."
