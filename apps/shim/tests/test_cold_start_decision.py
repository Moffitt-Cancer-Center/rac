"""Tests for rac_shim.cold_start.decision — pure cold-start branching.

Verifies: rac-v1.AC6.2 (cold-start interstitial logic).
"""

import pytest

from rac_shim.cold_start.decision import ColdStartDecision, decide

THRESHOLD = 2000  # ms


def test_200_fast_no_interstitial() -> None:
    d = decide(200, 50.0, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is False
    assert d.should_wake is False


def test_none_upstream_interstitial_and_wake() -> None:
    """Connection failure / timeout → cold start."""
    d = decide(None, None, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is True
    assert d.should_wake is True


def test_503_interstitial_and_wake() -> None:
    d = decide(503, 100.0, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is True
    assert d.should_wake is True


def test_504_interstitial_and_wake() -> None:
    d = decide(504, 100.0, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is True
    assert d.should_wake is True


def test_200_slow_over_threshold_no_interstitial() -> None:
    """A slow but completed 200 response is NOT a cold start.

    The response is already in hand — serving an interstitial now makes no
    sense.  The latency threshold is relevant for metrics/alerting, not for
    the interstitial decision on an already-completed response.
    """
    d = decide(200, 5000.0, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is False
    assert d.should_wake is False


def test_404_fast_no_interstitial() -> None:
    d = decide(404, 50.0, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is False
    assert d.should_wake is False


def test_500_no_interstitial() -> None:
    """A generic 500 is not treated as cold-start (only 503/504 are)."""
    d = decide(500, 50.0, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is False
    assert d.should_wake is False


def test_returns_dataclass() -> None:
    d = decide(200, 50.0, cold_start_threshold_ms=THRESHOLD)
    assert isinstance(d, ColdStartDecision)


def test_none_latency_with_503() -> None:
    """503 with no latency info still triggers interstitial."""
    d = decide(503, None, cold_start_threshold_ms=THRESHOLD)
    assert d.should_serve_interstitial is True
    assert d.should_wake is True
