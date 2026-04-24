"""Tests for services/provisioning/retry_policy.py — Functional Core.

Property tests: monotonic delay growth; permanent errors never retry.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError
from rac_control_plane.services.provisioning.retry_policy import RetryDecision, decide_retry


# ---------------------------------------------------------------------------
# Basic behaviour tests
# ---------------------------------------------------------------------------

def test_permanent_error_never_retries() -> None:
    err = ProvisioningError(code="aca_conflict", detail="conflict", retryable=False)
    decision = decide_retry(err, attempt=1, max_attempts=3)
    assert decision.should_retry is False
    assert decision.delay_seconds == 0.0


def test_transient_error_first_attempt_retries() -> None:
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=1, max_attempts=3)
    assert decision.should_retry is True
    assert decision.delay_seconds > 0


def test_transient_error_at_max_attempts_does_not_retry() -> None:
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=3, max_attempts=3)
    assert decision.should_retry is False


def test_transient_error_attempt_2_still_retries() -> None:
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=2, max_attempts=3)
    assert decision.should_retry is True


def test_delay_is_capped_at_300_seconds() -> None:
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=1, max_attempts=100)
    # Even for large attempts, delay should not exceed 300
    decision_large = decide_retry(err, attempt=50, max_attempts=100)
    assert decision_large.delay_seconds <= 300.0


def test_delay_formula_attempt_1() -> None:
    """2^1 * 10 = 20 seconds for attempt 1."""
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=1, max_attempts=10)
    assert decision.delay_seconds == 20.0


def test_delay_formula_attempt_2() -> None:
    """2^2 * 10 = 40 seconds for attempt 2."""
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=2, max_attempts=10)
    assert decision.delay_seconds == 40.0


def test_attempt_number_reflected_in_decision() -> None:
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=2, max_attempts=10)
    assert decision.attempt_number == 2


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

@given(
    attempt=st.integers(min_value=1, max_value=20),
)
@hyp_settings(max_examples=50)
def test_property_permanent_error_never_retries(attempt: int) -> None:
    err = ProvisioningError(code="dns_conflict", detail="conflict", retryable=False)
    decision = decide_retry(err, attempt=attempt, max_attempts=3)
    assert decision.should_retry is False


@given(
    attempt_a=st.integers(min_value=1, max_value=8),
    attempt_b=st.integers(min_value=1, max_value=8),
)
@hyp_settings(max_examples=100)
def test_property_delay_monotonically_increases(attempt_a: int, attempt_b: int) -> None:
    """Earlier attempts have smaller or equal delay than later attempts."""
    if attempt_a >= attempt_b:
        return  # only test when a < b
    err = TransientProvisioningError(code="aca_transient", detail="503")
    da = decide_retry(err, attempt=attempt_a, max_attempts=100)
    db = decide_retry(err, attempt=attempt_b, max_attempts=100)
    if da.should_retry and db.should_retry:
        assert da.delay_seconds <= db.delay_seconds


@given(
    attempt=st.integers(min_value=1, max_value=100),
)
@hyp_settings(max_examples=100)
def test_property_delay_never_exceeds_cap(attempt: int) -> None:
    err = TransientProvisioningError(code="aca_transient", detail="503")
    decision = decide_retry(err, attempt=attempt, max_attempts=200)
    if decision.should_retry:
        assert decision.delay_seconds <= 300.0
