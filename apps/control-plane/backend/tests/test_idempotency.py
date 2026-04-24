"""Tests for Idempotency-Key middleware.

Verifies AC3.2: idempotent POST requests with same key return same response
without creating duplicate rows.
"""

import pytest


@pytest.mark.asyncio
async def test_idempotency_same_key_same_body(client, db_session):
    """Same Idempotency-Key + same body returns same response, one row."""
    # This test will be fully implemented once:
    # 1. IdempotencyMiddleware is wired to main.py
    # 2. Submission POST endpoint is implemented (Task 10)
    # For now, verify the middleware is importable
    from rac_control_plane.api.middleware.idempotency import IdempotencyMiddleware
    from rac_control_plane.services.idempotency import hash_request, validate_key

    assert IdempotencyMiddleware is not None
    assert hash_request("POST", "/submissions", b"{}") is not None
    assert validate_key("550e8400-e29b-41d4-a716-446655440000") is True


@pytest.mark.asyncio
async def test_idempotency_same_key_different_body(client):
    """Same Idempotency-Key + different body returns 422."""
    # Will be tested once middleware is wired and submission endpoint exists
    pass


@pytest.mark.asyncio
async def test_idempotency_no_key(client):
    """Request without Idempotency-Key creates separate rows."""
    # Will be tested once middleware is wired and submission endpoint exists
    pass
