# pattern: Functional Core
"""Idempotency-Key service with pure cryptographic functions.

Per RFC 9457 draft, idempotency keys should be UUIDs or short alphanumeric strings.
This module provides pure functions for request hashing and key validation.
"""

import hashlib


def hash_request(method: str, path: str, body_bytes: bytes) -> str:
    """Compute SHA256 hash of request method, path, and body.

    Pure function with no side effects.

    Args:
        method: HTTP method (POST, PUT, DELETE, PATCH)
        path: Request path (e.g., /submissions)
        body_bytes: Request body as bytes

    Returns:
        SHA256 hex digest (64 chars)
    """
    combined = f"{method}:{path}".encode() + body_bytes
    return hashlib.sha256(combined).hexdigest()


def validate_key(key: str) -> bool:
    """Validate Idempotency-Key format per RFC 9457 draft.

    Pure validation: must be a UUID or alphanumeric string ≤ 256 chars.

    Args:
        key: The Idempotency-Key header value

    Returns:
        True if valid, False otherwise
    """
    # Length check
    if not key or len(key) > 256:
        return False

    # Must be alphanumeric (including dashes and underscores for UUID compatibility)
    return all(c.isalnum() or c in "-_" for c in key)
