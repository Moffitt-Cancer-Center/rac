"""Tests for access mode toggle service, validation, and API route.

Tests:
- Pure validation: property tests for can_set_public / can_set_token_required
- Service: set_access_mode updates app + inserts approval_event
- API: POST /apps/{app_id}/access-mode
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import (
    AccessMode,
    App,
    ApprovalEvent,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.errors import ForbiddenError, NotFoundError, ValidationApiError
from rac_control_plane.services.access_mode.toggle import set_access_mode
from rac_control_plane.services.access_mode.validation import (
    Invalid,
    Ok,
    can_set_public_with_status,
    can_set_token_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeApp:
    """Minimal App-like object for pure validation tests (no ORM session needed)."""

    def __init__(
        self,
        *,
        pi_principal_id: UUID | None = None,
        slug: str = "testslug",
        access_mode: AccessMode = AccessMode.token_required,
    ) -> None:
        self.id = uuid4()
        self.slug = slug
        self.pi_principal_id = pi_principal_id or uuid4()
        self.dept_fallback = "test"
        self.current_submission_id = None
        self.access_mode = access_mode


def _make_app(
    *,
    pi_principal_id: UUID | None = None,
    slug: str = "testslug",
    access_mode: AccessMode = AccessMode.token_required,
) -> Any:  # type: ignore[return]  # duck-typed App for validation functions
    return _FakeApp(pi_principal_id=pi_principal_id, slug=slug, access_mode=access_mode)


def _make_principal(
    oid: UUID | None = None,
    roles: frozenset[str] = frozenset(),
) -> Principal:
    return Principal(oid=oid or uuid4(), kind="user", roles=roles)


async def _create_app_with_submission(
    db_setup: AsyncSession,
    *,
    owner_oid: UUID,
    slug: str,
    status: SubmissionStatus = SubmissionStatus.deployed,
) -> tuple[UUID, UUID]:
    """Insert App + Submission, return (app_id, sub_id)."""
    sub_id = uuid4()
    app_id = uuid4()

    sub = Submission(
        id=sub_id,
        slug=slug,
        status=status,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid,
        dept_fallback="TestDept",
    )
    db_setup.add(sub)
    await db_setup.flush()

    app = App(
        id=app_id,
        slug=slug,
        pi_principal_id=owner_oid,
        dept_fallback="TestDept",
        current_submission_id=sub_id,
    )
    db_setup.add(app)
    await db_setup.flush()
    await db_setup.commit()
    return app_id, sub_id


@pytest.fixture(autouse=True)
def patch_toggle_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest_settings_helper import make_test_settings
    settings = make_test_settings()
    monkeypatch.setattr(
        "rac_control_plane.services.access_mode.toggle.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "rac_control_plane.api.routes.access_mode.set_access_mode",
        set_access_mode,  # No-op patch: keep using the real function
    )


# ---------------------------------------------------------------------------
# Pure validation tests (concrete)
# ---------------------------------------------------------------------------

def test_can_set_public_owner_deployed() -> None:
    """App owner may set public on a deployed app."""
    oid = uuid4()
    app = _make_app(pi_principal_id=oid)
    principal = _make_principal(oid=oid)
    result = can_set_public_with_status(
        app, principal, None,
        submission_status=SubmissionStatus.deployed,
    )
    assert isinstance(result, Ok)


def test_can_set_public_not_deployed() -> None:
    """Approved-but-not-deployed → not_deployed."""
    oid = uuid4()
    app = _make_app(pi_principal_id=oid)
    principal = _make_principal(oid=oid)
    result = can_set_public_with_status(
        app, principal, None,
        submission_status=SubmissionStatus.approved,
    )
    assert isinstance(result, Invalid)
    assert result.reason == "not_deployed"


def test_can_set_public_no_submission() -> None:
    """No submission at all → not_deployed."""
    oid = uuid4()
    app = _make_app(pi_principal_id=oid)
    principal = _make_principal(oid=oid)
    result = can_set_public_with_status(
        app, principal, None,
        submission_status=None,
    )
    assert isinstance(result, Invalid)
    assert result.reason == "not_deployed"


def test_can_set_public_non_owner_no_admin() -> None:
    """Non-owner non-admin → not_authorized."""
    app = _make_app()
    principal = _make_principal()
    result = can_set_public_with_status(
        app, principal, None,
        submission_status=SubmissionStatus.deployed,
    )
    assert isinstance(result, Invalid)
    assert result.reason == "not_authorized"


def test_can_set_public_admin_deployed() -> None:
    """Admin can set public on any deployed app."""
    app = _make_app()
    principal = _make_principal(roles=frozenset(["it_approver"]))
    result = can_set_public_with_status(
        app, principal, None,
        submission_status=SubmissionStatus.deployed,
    )
    assert isinstance(result, Ok)


def test_can_set_token_required_owner() -> None:
    """Owner can always flip back to token_required."""
    oid = uuid4()
    app = _make_app(pi_principal_id=oid)
    principal = _make_principal(oid=oid)
    result = can_set_token_required(app, principal, None)
    assert isinstance(result, Ok)


def test_can_set_token_required_non_owner() -> None:
    """Non-owner non-admin → not_authorized."""
    app = _make_app()
    principal = _make_principal()
    result = can_set_token_required(app, principal, None)
    assert isinstance(result, Invalid)
    assert result.reason == "not_authorized"


# ---------------------------------------------------------------------------
# Property tests: no principal without owner/admin ever gets Ok for public
# ---------------------------------------------------------------------------

_uuids = st.uuids()
_role_sets = st.frozensets(st.text(min_size=1, max_size=20), max_size=3)


@given(
    pi_oid=_uuids,
    actor_oid=_uuids,
    actor_roles=_role_sets,
)
@settings(max_examples=100)
def test_property_non_owner_non_admin_never_ok_public(
    pi_oid: UUID,
    actor_oid: UUID,
    actor_roles: frozenset[str],
) -> None:
    """No principal without PI oid or it_approver role gets Ok for public."""
    # Skip cases where actor is the PI
    if actor_oid == pi_oid:
        return
    # Skip if actor has admin role
    if "it_approver" in actor_roles:
        return

    app = _make_app(pi_principal_id=pi_oid)
    principal = Principal(oid=actor_oid, kind="user", roles=actor_roles)
    result = can_set_public_with_status(
        app, principal, None,
        submission_status=SubmissionStatus.deployed,
    )
    assert isinstance(result, Invalid)


@given(
    pi_oid=_uuids,
    actor_oid=_uuids,
    actor_roles=_role_sets,
)
@settings(max_examples=100)
def test_property_non_owner_non_admin_never_ok_token_required(
    pi_oid: UUID,
    actor_oid: UUID,
    actor_roles: frozenset[str],
) -> None:
    """No principal without PI oid or it_approver role gets Ok for token_required."""
    if actor_oid == pi_oid:
        return
    if "it_approver" in actor_roles:
        return

    app = _make_app(pi_principal_id=pi_oid)
    principal = Principal(oid=actor_oid, kind="user", roles=actor_roles)
    result = can_set_token_required(app, principal, None)
    assert isinstance(result, Invalid)


# ---------------------------------------------------------------------------
# Service tests (DB)
# ---------------------------------------------------------------------------

async def test_set_public_on_deployed(db_session: AsyncSession) -> None:
    """set_access_mode('public') on deployed app updates app.access_mode."""
    owner_oid = uuid4()
    sub_id = uuid4()
    app_id = uuid4()

    sub = Submission(
        id=sub_id, slug="svc-pub-slug",
        status=SubmissionStatus.deployed,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/x/y",
        git_ref="main", dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid, dept_fallback="Dept",
    )
    db_session.add(sub)
    await db_session.flush()  # flush sub before app FK reference

    app = App(
        id=app_id, slug="svc-pub-slug",
        pi_principal_id=owner_oid, dept_fallback="Dept",
        current_submission_id=sub_id,
    )
    db_session.add(app)
    await db_session.flush()

    updated = await set_access_mode(
        db_session,
        app_id=app_id,
        new_mode="public",
        actor_principal_id=owner_oid,
        actor_roles=frozenset(),
        notes="Opening for review period",
    )
    assert updated.access_mode == AccessMode.public


async def test_set_public_inserts_approval_event(db_session: AsyncSession) -> None:
    """set_access_mode inserts an approval_event with kind='access_mode_changed'."""
    owner_oid = uuid4()
    sub_id = uuid4()
    app_id = uuid4()

    sub = Submission(
        id=sub_id, slug="svc-evt-slug",
        status=SubmissionStatus.deployed,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/x/y",
        git_ref="main", dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid, dept_fallback="Dept",
    )
    db_session.add(sub)
    await db_session.flush()

    app = App(
        id=app_id, slug="svc-evt-slug",
        pi_principal_id=owner_oid, dept_fallback="Dept",
        current_submission_id=sub_id,
    )
    db_session.add(app)
    await db_session.flush()

    await set_access_mode(
        db_session,
        app_id=app_id,
        new_mode="public",
        actor_principal_id=owner_oid,
        actor_roles=frozenset(),
        notes="Event check notes",
    )

    stmt = select(ApprovalEvent).where(
        ApprovalEvent.kind == "access_mode_changed",
        ApprovalEvent.actor_principal_id == owner_oid,
    )
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) >= 1
    evt = events[-1]
    assert evt.payload is not None
    assert evt.payload["to"] == "public"


async def test_set_public_on_approved_raises_validation_error(db_session: AsyncSession) -> None:
    """set_access_mode('public') on approved-but-not-deployed raises ValidationApiError."""
    owner_oid = uuid4()
    sub_id = uuid4()
    app_id = uuid4()

    sub = Submission(
        id=sub_id, slug="svc-noname-slug",
        status=SubmissionStatus.approved,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/x/y",
        git_ref="main", dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid, dept_fallback="Dept",
    )
    db_session.add(sub)
    await db_session.flush()

    app = App(
        id=app_id, slug="svc-noname-slug",
        pi_principal_id=owner_oid, dept_fallback="Dept",
        current_submission_id=sub_id,
    )
    db_session.add(app)
    await db_session.flush()

    with pytest.raises(ValidationApiError) as exc_info:
        await set_access_mode(
            db_session,
            app_id=app_id,
            new_mode="public",
            actor_principal_id=owner_oid,
            actor_roles=frozenset(),
            notes="Should fail not deployed",
        )
    assert exc_info.value.code == "not_deployed"


async def test_non_owner_raises_forbidden(db_session: AsyncSession) -> None:
    """Non-owner set_access_mode raises ForbiddenError."""
    owner_oid = uuid4()
    stranger_oid = uuid4()
    sub_id = uuid4()
    app_id = uuid4()

    sub = Submission(
        id=sub_id, slug="svc-forbidden-slug",
        status=SubmissionStatus.deployed,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/x/y",
        git_ref="main", dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid, dept_fallback="Dept",
    )
    db_session.add(sub)
    await db_session.flush()

    app = App(
        id=app_id, slug="svc-forbidden-slug",
        pi_principal_id=owner_oid, dept_fallback="Dept",
        current_submission_id=sub_id,
    )
    db_session.add(app)
    await db_session.flush()

    with pytest.raises(ForbiddenError):
        await set_access_mode(
            db_session,
            app_id=app_id,
            new_mode="public",
            actor_principal_id=stranger_oid,
            actor_roles=frozenset(),
            notes="Forbidden check notes",
        )


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------

async def test_api_owner_sets_public_on_deployed_200(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """POST /access-mode → 200, app.access_mode='public', approval_event inserted."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid, slug=f"am-{uuid4().hex[:8]}"
    )
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "public", "notes": "Opening for review period"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["access_mode"] == "public"


async def test_api_owner_sets_public_on_approved_422(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """POST /access-mode with mode=public on approved (not deployed) → 422."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid,
        slug=f"am-{uuid4().hex[:8]}",
        status=SubmissionStatus.approved,
    )
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "public", "notes": "Should fail not deployed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_api_non_owner_non_admin_403(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Non-owner non-admin → 403."""
    owner_oid = uuid4()
    stranger_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid, slug=f"am-{uuid4().hex[:8]}"
    )
    token = mock_oidc.issue_user_token(oid=stranger_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "public", "notes": "Unauthorized attempt"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_api_invalid_mode_422(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Invalid mode value → 422 (Pydantic validation)."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid, slug=f"am-{uuid4().hex[:8]}"
    )
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "secret_mode", "notes": "Invalid mode notes"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_api_flip_back_to_token_required_200(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """After setting public, flipping back to token_required → 200."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid, slug=f"am-{uuid4().hex[:8]}"
    )
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "public", "notes": "First flip to public"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "token_required", "notes": "Flip back to token required"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_mode"] == "token_required"


async def test_api_notes_too_short_422(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """notes shorter than 10 chars → 422."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid, slug=f"am-{uuid4().hex[:8]}"
    )
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/access-mode",
        json={"mode": "public", "notes": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
