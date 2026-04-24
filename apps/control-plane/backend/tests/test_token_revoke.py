"""Tests for services/tokens/revoke.py and listing.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from rac_control_plane.data.models import ApprovalEvent, ReviewerToken, RevokedToken
from rac_control_plane.errors import NotFoundError
from rac_control_plane.services.tokens.listing import TokenListRow, list_tokens_for_app
from rac_control_plane.services.tokens.revoke import revoke_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _insert_app(session: object, app_id: UUID, slug: str) -> None:
    from rac_control_plane.data.models import App
    app = App(
        id=app_id,
        slug=slug,
        pi_principal_id=uuid4(),
        dept_fallback="test",
    )
    session.add(app)
    await session.flush()


async def _insert_reviewer_token(
    session: object,
    *,
    app_id: UUID,
    jti: UUID,
    reviewer_label: str = "Reviewer #1",
    ttl_days: int = 30,
) -> ReviewerToken:
    now = datetime.now(UTC)
    exp = now + timedelta(days=ttl_days)
    rt = ReviewerToken(
        id=uuid4(),
        principal_id=uuid4(),
        jti=str(jti),
        app_id=app_id,
        reviewer_label=reviewer_label,
        kid="rac-app-testapp-v1",
        issued_by_principal_id=uuid4(),
        expires_at=exp,
        scope="read",
    )
    session.add(rt)
    await session.flush()
    return rt


# ---------------------------------------------------------------------------
# Revoke tests
# ---------------------------------------------------------------------------

async def test_revoke_existing_inserts_revoked_token(db_session: object) -> None:
    """Revoking an existing token inserts a revoked_token row."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "revokeapp")
    jti = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti)

    actor = uuid4()
    await revoke_token(db_session, jti=jti, actor_principal_id=actor, reason="test reason")

    stmt = select(RevokedToken).where(RevokedToken.jti == str(jti))
    revoked = (await db_session.execute(stmt)).scalar_one()
    assert revoked.jti == str(jti)
    assert revoked.revoked_by_principal_id == actor
    assert revoked.reason == "test reason"
    assert revoked.expires_at is not None


async def test_revoke_existing_inserts_approval_event(db_session: object) -> None:
    """Revoking a token inserts an approval_event with kind='reviewer_token_revoked'."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "revtevtapp")
    jti = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti)

    actor = uuid4()
    await revoke_token(db_session, jti=jti, actor_principal_id=actor, reason=None)

    stmt = select(ApprovalEvent).where(
        ApprovalEvent.kind == "reviewer_token_revoked",
        ApprovalEvent.actor_principal_id == actor,
    )
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) >= 1
    evt = events[-1]
    assert evt.payload is not None
    assert evt.payload["jti"] == str(jti)


async def test_revoke_nonexistent_raises_not_found(db_session: object) -> None:
    """Revoking a non-existent jti raises NotFoundError."""
    with pytest.raises(NotFoundError):
        await revoke_token(
            db_session,
            jti=uuid4(),
            actor_principal_id=uuid4(),
            reason=None,
        )


async def test_revoke_preserves_expires_at(db_session: object) -> None:
    """The revoked_token.expires_at matches the original token's expires_at."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "expapp")
    jti = uuid4()
    rt = await _insert_reviewer_token(db_session, app_id=app_id, jti=jti, ttl_days=14)

    await revoke_token(db_session, jti=jti, actor_principal_id=uuid4(), reason=None)

    stmt = select(RevokedToken).where(RevokedToken.jti == str(jti))
    revoked = (await db_session.execute(stmt)).scalar_one()
    # expires_at on the revoked row should match the original token
    assert revoked.expires_at == rt.expires_at


# ---------------------------------------------------------------------------
# Listing tests
# ---------------------------------------------------------------------------

async def test_list_excludes_revoked_by_default(db_session: object) -> None:
    """list_tokens_for_app with include_revoked=False excludes revoked tokens."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "listapp")
    jti_active = uuid4()
    jti_revoked = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti_active, reviewer_label="Active")
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti_revoked, reviewer_label="Revoked")

    # Revoke the second one
    await revoke_token(db_session, jti=jti_revoked, actor_principal_id=uuid4(), reason=None)

    rows = await list_tokens_for_app(db_session, app_id=app_id, include_revoked=False)
    jtis = {r.jti for r in rows}
    assert jti_active in jtis
    assert jti_revoked not in jtis


async def test_list_includes_revoked_when_requested(db_session: object) -> None:
    """list_tokens_for_app with include_revoked=True includes revoked tokens."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "listapp2")
    jti_active = uuid4()
    jti_revoked = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti_active, reviewer_label="Active2")
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti_revoked, reviewer_label="Revoked2")

    await revoke_token(db_session, jti=jti_revoked, actor_principal_id=uuid4(), reason=None)

    rows = await list_tokens_for_app(db_session, app_id=app_id, include_revoked=True)
    jtis = {r.jti for r in rows}
    assert jti_active in jtis
    assert jti_revoked in jtis


async def test_list_revoked_at_is_set(db_session: object) -> None:
    """Revoked tokens have revoked_at populated in the listing."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "listapp3")
    jti = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti, reviewer_label="Revoked3")

    await revoke_token(db_session, jti=jti, actor_principal_id=uuid4(), reason=None)

    rows = await list_tokens_for_app(db_session, app_id=app_id, include_revoked=True)
    revoked_rows = [r for r in rows if r.jti == jti]
    assert len(revoked_rows) == 1
    assert revoked_rows[0].revoked_at is not None


async def test_list_active_revoked_at_is_none(db_session: object) -> None:
    """Active tokens have revoked_at=None in the listing."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "listapp4")
    jti = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti, reviewer_label="Active4")

    rows = await list_tokens_for_app(db_session, app_id=app_id, include_revoked=False)
    active_rows = [r for r in rows if r.jti == jti]
    assert len(active_rows) == 1
    assert active_rows[0].revoked_at is None


async def test_list_returns_token_list_rows(db_session: object) -> None:
    """list_tokens_for_app returns TokenListRow instances."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "listapp5")
    jti = uuid4()
    await _insert_reviewer_token(db_session, app_id=app_id, jti=jti, reviewer_label="Type Check")

    rows = await list_tokens_for_app(db_session, app_id=app_id)
    assert all(isinstance(r, TokenListRow) for r in rows)


async def test_list_empty_for_unknown_app(db_session: object) -> None:
    """list_tokens_for_app returns an empty list for an unknown app_id."""
    rows = await list_tokens_for_app(db_session, app_id=uuid4())
    assert rows == []
