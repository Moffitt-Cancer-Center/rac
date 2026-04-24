# pattern: Functional Core
"""Inbound HMAC signature verification for pipeline callbacks.

Mirrors the contract of rac-pipeline/scripts/hmac_sign.py — kept here as a
separate copy so the control plane has no cross-repo import dependency.

Signature format: ``sha256=<hex(HMAC-SHA256(secret, "{timestamp}.{body}"))>``

The timestamp window check rejects both stale (> max_age_seconds old) and
future-dated timestamps (> 60 s ahead of ``now``) to limit replay attacks.
"""

import hashlib
import hmac
from datetime import UTC, datetime


class SignatureInvalid(Exception):  # noqa: N818
    """Raised when an HMAC signature fails verification or the timestamp is stale."""


def compute_signature(secret: bytes, timestamp: str, body: bytes) -> str:
    """Compute an HMAC-SHA256 signature for the given payload.

    Args:
        secret:    Raw HMAC key bytes.
        timestamp: ISO-8601 UTC timestamp string (the ``X-RAC-Timestamp`` header value).
        body:      Raw request body bytes — sign the exact bytes, never a parsed repr.

    Returns:
        ``"sha256=" + hex digest``
    """
    msg = timestamp.encode() + b"." + body
    digest = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    expected_header: str,
    secret: bytes,
    timestamp: str,
    body: bytes,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 300,
) -> None:
    """Verify an inbound HMAC signature and timestamp freshness.

    Args:
        expected_header: Value of the ``X-RAC-Signature-256`` header.
        secret:          Raw HMAC key bytes.
        timestamp:       Value of the ``X-RAC-Timestamp`` header (ISO-8601 UTC).
        body:            Raw request body bytes.
        now:             Override current time (for testing). Defaults to UTC now.
        max_age_seconds: Maximum age of a valid timestamp. Defaults to 300 s (5 min).

    Raises:
        SignatureInvalid: On HMAC mismatch, timestamp parse failure, stale
                         timestamp, or timestamp more than 60 s in the future.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    # Parse timestamp — must be ISO-8601 with timezone
    try:
        ts_dt = datetime.fromisoformat(timestamp)
        if ts_dt.tzinfo is None:
            # Assume UTC if no timezone info present
            ts_dt = ts_dt.replace(tzinfo=UTC)
    except ValueError as exc:
        raise SignatureInvalid(f"Unparseable timestamp: {timestamp!r}") from exc

    age_seconds = (now - ts_dt).total_seconds()

    # Reject stale timestamps
    if age_seconds > max_age_seconds:
        raise SignatureInvalid(
            f"Timestamp is too old: {age_seconds:.0f}s > {max_age_seconds}s"
        )

    # Reject timestamps too far in the future (replay-window guard)
    if age_seconds < -60:
        raise SignatureInvalid(
            f"Timestamp is too far in the future: {-age_seconds:.0f}s ahead"
        )

    # Constant-time HMAC comparison
    expected_sig = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected_header, expected_sig):
        raise SignatureInvalid("HMAC signature mismatch")
