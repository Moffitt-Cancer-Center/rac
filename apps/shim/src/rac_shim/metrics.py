# pattern: Imperative Shell
"""OpenTelemetry metric instruments for the shim.

Verifies: rac-v1.AC10.2 (shim portion)

Instruments:
- ``rac.shim.token_validations`` counter: labeled by result
  (valid | expired | revoked | malformed).
- ``rac.shim.wake_up_duration_ms`` histogram: wall-clock ms from
  cold-start interstitial to first upstream response.

Call sites:
- ``token_validation_counter.add(1, {"result": "<result>"})`` in main.py
  after each validation outcome.
- ``wake_up_duration_histogram.record(elapsed_ms)`` in the caller that
  receives the wake() return value.
"""
from __future__ import annotations

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)

_meter = metrics.get_meter("rac.shim", version="1.0.0")

token_validation_counter = _meter.create_counter(
    name="rac.shim.token_validations",
    description="Count of token validation attempts, labeled by result.",
    unit="1",
)

wake_up_duration_histogram = _meter.create_histogram(
    name="rac.shim.wake_up_duration_ms",
    description="Wall-clock ms from cold-start interstitial to upstream 200.",
    unit="ms",
)


def configure_metrics(otlp_endpoint: str = "http://localhost:4317") -> MeterProvider:
    """Set up an OTLP MeterProvider and register it globally.

    Returns the configured MeterProvider (useful for testing with
    InMemoryMetricReader).

    In production this is called once during lifespan startup.
    """
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
    except Exception:
        # Fall back to console exporter if OTLP isn't available.
        exporter = ConsoleMetricExporter()  # type: ignore[assignment]

    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return provider
