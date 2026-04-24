# pattern: Functional Core
"""Outbound HMAC signing for webhook delivery.

Intentionally separate from ``verify.py`` so outbound code paths never import
inbound verification functions.  The algorithm is identical — see verify.py for
details — but the boundary is kept explicit.
"""

import hashlib
import hmac
from datetime import UTC, datetime


def compute_signature(secret: bytes, timestamp: str, body: bytes) -> str:
    """Compute an HMAC-SHA256 signature for an outbound webhook payload.

    Args:
        secret:    Raw HMAC key bytes (per-subscription Key Vault secret).
        timestamp: ISO-8601 UTC timestamp string sent in ``X-RAC-Timestamp``.
        body:      Canonical JSON bytes (sorted keys, no extra whitespace).

    Returns:
        ``"sha256=" + hex digest``
    """
    msg = timestamp.encode() + b"." + body
    digest = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def sign_payload(
    secret: bytes,
    body: bytes,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Sign a payload and return (timestamp, signature) headers.

    Args:
        secret: Raw HMAC key bytes.
        body:   Canonical JSON bytes to sign.
        now:    Override timestamp (for testing). Defaults to UTC now.

    Returns:
        (timestamp_str, signature_str) where timestamp_str is ISO-8601 UTC
        and signature_str is ``"sha256=<hex>"``.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    timestamp = now.isoformat()
    signature = compute_signature(secret, timestamp, body)
    return timestamp, signature
