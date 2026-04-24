# pattern: Functional Core
"""Retry policy for provisioning errors.

Pure functions that decide whether to retry based on error type and attempt count.
"""

from __future__ import annotations

from dataclasses import dataclass

from rac_control_plane.provisioning.aca import ProvisioningError


@dataclass(frozen=True)
class RetryDecision:
    """Decision whether and when to retry a provisioning step."""

    should_retry: bool
    delay_seconds: float
    attempt_number: int


def decide_retry(
    error: ProvisioningError,
    attempt: int,
    *,
    max_attempts: int = 3,
) -> RetryDecision:
    """Decide whether to retry a provisioning step.

    Strategy:
    - Permanent errors (retryable=False) → never retry.
    - Transient errors with attempt < max_attempts → retry with exponential backoff
      (2^attempt * 10 seconds, capped at 300 seconds).

    Args:
        error: The provisioning error that occurred.
        attempt: Current attempt number (1-based: first try = 1).
        max_attempts: Maximum number of attempts total.

    Returns:
        RetryDecision with should_retry, delay_seconds, attempt_number.
    """
    if not error.retryable:
        return RetryDecision(
            should_retry=False,
            delay_seconds=0.0,
            attempt_number=attempt,
        )

    if attempt >= max_attempts:
        return RetryDecision(
            should_retry=False,
            delay_seconds=0.0,
            attempt_number=attempt,
        )

    # Exponential backoff: 2^attempt * 10s, capped at 300s
    delay = min(2**attempt * 10.0, 300.0)
    return RetryDecision(
        should_retry=True,
        delay_seconds=delay,
        attempt_number=attempt,
    )
