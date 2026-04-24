"""Tests for rac_shim.metrics (AC10.2 shim portion).

Uses InMemoryMetricReader to capture OTel metrics without a real exporter.
"""
from __future__ import annotations

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def _make_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    """Build a fresh MeterProvider backed by an InMemoryMetricReader."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return provider, reader


def _get_data_points(
    reader: InMemoryMetricReader,
    metric_name: str,
) -> list[object]:
    """Extract data points for the named metric from the reader snapshot."""
    data = reader.get_metrics_data()
    points: list[object] = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == metric_name:
                    # Handle Sum (Counter) and Histogram types
                    dp_list = getattr(m.data, "data_points", [])
                    points.extend(dp_list)
    return points


def test_token_validation_counter_increments() -> None:
    """token_validation_counter increments correctly per result label."""
    provider, reader = _make_provider()
    meter = provider.get_meter("rac.shim.test")
    counter = meter.create_counter("rac.shim.token_validations")

    counter.add(1, {"result": "valid"})
    counter.add(1, {"result": "valid"})
    counter.add(1, {"result": "expired"})
    counter.add(1, {"result": "revoked"})
    counter.add(1, {"result": "malformed"})

    points = _get_data_points(reader, "rac.shim.token_validations")
    assert len(points) > 0

    # Group by result attribute and check values
    by_result: dict[str, int] = {}
    for dp in points:
        result = dp.attributes.get("result", "")  # type: ignore[attr-defined]
        by_result[result] = int(dp.value)  # type: ignore[attr-defined]

    assert by_result.get("valid", 0) == 2
    assert by_result.get("expired", 0) == 1
    assert by_result.get("revoked", 0) == 1
    assert by_result.get("malformed", 0) == 1


def test_wake_up_duration_histogram_records_value() -> None:
    """wake_up_duration_histogram records a positive value."""
    provider, reader = _make_provider()
    meter = provider.get_meter("rac.shim.test")
    histogram = meter.create_histogram("rac.shim.wake_up_duration_ms")

    histogram.record(1250.5)
    histogram.record(800.0)

    points = _get_data_points(reader, "rac.shim.wake_up_duration_ms")
    assert len(points) > 0
    # Sum or count of recorded observations
    dp = points[0]
    assert dp.count >= 2  # type: ignore[attr-defined]
    assert dp.sum > 0  # type: ignore[attr-defined]


def test_counter_all_result_labels_supported() -> None:
    """All four expected result labels can be recorded."""
    provider, reader = _make_provider()
    meter = provider.get_meter("rac.shim.test")
    counter = meter.create_counter("rac.shim.token_validations")

    for label in ("valid", "expired", "revoked", "malformed"):
        counter.add(1, {"result": label})

    points = _get_data_points(reader, "rac.shim.token_validations")
    labels_seen = {dp.attributes.get("result") for dp in points}  # type: ignore[attr-defined]
    assert {"valid", "expired", "revoked", "malformed"}.issubset(labels_seen)


def test_histogram_zero_value_is_recorded() -> None:
    """Histogram accepts 0 as a valid observation."""
    provider, reader = _make_provider()
    meter = provider.get_meter("rac.shim.test")
    histogram = meter.create_histogram("rac.shim.wake_up_duration_ms")

    histogram.record(0.0)
    points = _get_data_points(reader, "rac.shim.wake_up_duration_ms")
    assert len(points) > 0
