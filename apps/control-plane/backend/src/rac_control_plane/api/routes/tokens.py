# pattern: Imperative Shell
"""Token management API routes.

POST   /apps/{app_id}/tokens         — mint a reviewer token
GET    /apps/{app_id}/tokens         — list tokens (without JWT)
DELETE /apps/{app_id}/tokens/{jti}   — revoke a token
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.tokens import (
    TokenCreateRequest,
    TokenCreateResponse,
    TokenListItemResponse,
    TokenListResponse,
)
from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import App, Submission
from rac_control_plane.errors import ForbiddenError, NotFoundError
from rac_control_plane.services.tokens.issuer import issue_reviewer_token
from rac_control_plane.services.tokens.key_probe import SignatureFormat
from rac_control_plane.services.tokens.listing import list_tokens_for_app
from rac_control_plane.services.tokens.revoke import revoke_token
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/apps", tags=["tokens"])

# Test-only: set this to an async callable (digest: bytes) -> bytes to bypass KV.
# In production this is always None and the real KV signer is used.
_test_signer_override: Callable[[bytes], Awaitable[bytes]] | None = None


async def _get_app_or_404(session: AsyncSession, app_id: UUID) -> App:
    """Fetch App by id or raise NotFoundError."""
    result = await session.execute(select(App).where(App.id == app_id))
    app = result.scalar_one_or_none()
    if app is None:
        raise NotFoundError(public_message=f"App {app_id} not found.")
    return app


async def _get_submission_owner(session: AsyncSession, submission_id: UUID | None) -> UUID | None:
    """Return the submitter_principal_id of the given submission, or None."""
    if submission_id is None:
        return None
    result = await session.execute(
        select(Submission.submitter_principal_id).where(Submission.id == submission_id)
    )
    row = result.scalar_one_or_none()
    return row


def _is_app_owner_or_admin(
    app: App,
    principal: Principal,
    submitter_principal_id: UUID | None,
    *,
    admin_role: str,
) -> bool:
    """Return True if principal is the PI, the submitter, or has the admin role."""
    if admin_role in principal.roles:
        return True
    if principal.oid == app.pi_principal_id:
        return True
    if submitter_principal_id is not None and principal.oid == submitter_principal_id:
        return True
    return False


@router.post("/{app_id}/tokens", status_code=201, response_model=TokenCreateResponse)
async def mint_token(
    app_id: UUID,
    body: TokenCreateRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    session: AsyncSession = Depends(get_session),
) -> TokenCreateResponse:
    """Mint a reviewer token for the given app.

    The JWT is returned ONCE in this response. It cannot be re-fetched.
    The visit_url encodes the token as a query parameter for one-click reviewer access.

    Auth: app PI, current submitter, or admin.

    Raises:
        404: App not found.
        403: Not the app owner or admin.
        422: ttl_days out of range or reviewer_label empty.
    """
    settings = get_settings()

    app = await _get_app_or_404(session, app_id)
    submitter_oid = await _get_submission_owner(session, app.current_submission_id)

    if not _is_app_owner_or_admin(
        app, principal, submitter_oid, admin_role=settings.approver_role_it
    ):
        raise ForbiddenError(public_message="Only the app owner or admin may mint reviewer tokens.")

    # Determine signing params — use probed format if available, else RAW_R_S
    sig_format: SignatureFormat | None = None
    try:
        from rac_control_plane.services.tokens.key_probe import get_detected_format
        sig_format = get_detected_format()
    except RuntimeError:
        sig_format = None  # issuer will default to RAW_R_S

    # Use test signer override if set (test-only); otherwise None → production KV path
    signer = _test_signer_override

    issued = await issue_reviewer_token(
        session,
        app_id=app_id,
        app_slug=app.slug,
        reviewer_label=body.reviewer_label,
        ttl_days=body.ttl_days,
        actor_principal_id=principal.oid,
        signer=signer,
        signature_format=sig_format,
        issuer=settings.issuer or f"https://{settings.parent_domain}",
    )

    await session.commit()

    visit_url = (
        f"https://{app.slug}.{settings.parent_domain}/"
        f"?rac_token={issued.jwt}"
    )

    return TokenCreateResponse(
        jwt=issued.jwt,
        jti=issued.jti,
        expires_at=issued.expires_at,
        reviewer_label=issued.reviewer_label,
        visit_url=visit_url,
    )


@router.get("/{app_id}/tokens", response_model=TokenListResponse)
async def list_app_tokens(
    app_id: UUID,
    principal: Annotated[Principal, Depends(current_principal)],
    include_revoked: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> TokenListResponse:
    """List reviewer tokens for the given app (JWT omitted).

    Auth: app PI, current submitter, or admin.

    Raises:
        404: App not found.
        403: Not authorized.
    """
    settings = get_settings()
    app = await _get_app_or_404(session, app_id)
    submitter_oid = await _get_submission_owner(session, app.current_submission_id)

    if not _is_app_owner_or_admin(
        app, principal, submitter_oid, admin_role=settings.approver_role_it
    ):
        raise ForbiddenError(public_message="Only the app owner or admin may list tokens.")

    rows = await list_tokens_for_app(session, app_id=app_id, include_revoked=include_revoked)

    return TokenListResponse(
        items=[
            TokenListItemResponse(
                jti=r.jti,
                reviewer_label=r.reviewer_label,
                issued_at=r.issued_at,
                expires_at=r.expires_at,
                revoked_at=r.revoked_at,
                scope=r.scope,
                issued_by_principal_id=r.issued_by_principal_id,
            )
            for r in rows
        ]
    )


@router.delete("/{app_id}/tokens/{jti}", status_code=204)
async def delete_token(
    app_id: UUID,
    jti: UUID,
    principal: Annotated[Principal, Depends(current_principal)],
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke a reviewer token.

    Auth: app PI, current submitter, or admin.

    Raises:
        404: App or token not found.
        403: Not authorized.
    """
    settings = get_settings()
    app = await _get_app_or_404(session, app_id)
    submitter_oid = await _get_submission_owner(session, app.current_submission_id)

    if not _is_app_owner_or_admin(
        app, principal, submitter_oid, admin_role=settings.approver_role_it
    ):
        raise ForbiddenError(public_message="Only the app owner or admin may revoke tokens.")

    await revoke_token(session, jti=jti, actor_principal_id=principal.oid, reason=None)
    await session.commit()
