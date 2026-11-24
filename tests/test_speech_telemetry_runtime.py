import unittest
from unittest.mock import Mock, patch

from voxpipe.speech.telemetry_runtime import (
    SpeechTelemetryRuntime,
    configure_speech_otel,
)


class SpeechTelemetryRuntimeConfigTests(unittest.TestCase):
    def test_rejects_blank_service_name_before_loading_optional_sdk(self):
        with patch("builtins.__import__", side_effect=AssertionError("SDK import should be lazy")):
            with self.assertRaisesRegex(ValueError, "service_name"):
                configure_speech_otel(service_name="  ")

    def test_rejects_non_finite_or_out_of_range_sample_ratio(self):
        for ratio in (float("nan"), float("inf"), -0.01, 1.01, True):
            with self.subTest(ratio=ratio):
                with self.assertRaisesRegex(ValueError, "trace_sample_ratio"):
                    configure_speech_otel(service_name="voxpipe-worker", trace_sample_ratio=ratio)

    def test_rejects_invalid_metric_export_interval(self):
        for interval in (999, 300_001, 1.5, True):
            with self.subTest(interval=interval):
                with self.assertRaisesRegex(ValueError, "metric_export_interval_ms"):
                    configure_speech_otel(
                        service_name="voxpipe-worker",
                        metric_export_interval_ms=interval,
                    )

    def test_shutdown_attempts_both_providers_and_is_idempotent(self):
        meter_provider, tracer_provider = Mock(), Mock()
        meter_provider.shutdown.side_effect = RuntimeError("metric exporter failed")
        runtime = SpeechTelemetryRuntime(
            observer=Mock(),
            _meter_provider=meter_provider,
            _tracer_provider=tracer_provider,
        )
        with self.assertRaisesRegex(RuntimeError, "failed to shut down"):
            runtime.shutdown()
        runtime.shutdown()
        meter_provider.shutdown.assert_called_once_with()
        tracer_provider.shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
