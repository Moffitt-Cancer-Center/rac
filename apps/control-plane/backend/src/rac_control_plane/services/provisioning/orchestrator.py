# pattern: Imperative Shell
"""Tier 3 provisioning orchestrator.

Sequences all Azure SDK calls to provision a researcher app:
1. Upsert app row (atomic ON CONFLICT slug DO UPDATE current_submission_id).
2. Build tags.
3. Ensure Azure Files share.
4. Create signing key (skipped if already exists for this app).
5. Create/update ACA app.
6. Upsert DNS A record.
7. Transition submission approved → deployed.
8. Write approval_event rows.
9. Commit.

On ProvisioningError: uses retry_policy to decide whether to retry.
On permanent failure: write provisioning_failed event, leave submission at approved.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.app_repo import upsert_app_for_approved_submission
from rac_control_plane.data.models import (
    ApprovalEvent,
    SigningKeyVersion,
    Submission,
)
from rac_control_plane.provisioning.aca import ProvisioningError
from rac_control_plane.provisioning.tag_builder import build_tier3_tags
from rac_control_plane.services.provisioning.retry_policy import decide_retry
from rac_control_plane.services.submissions.fsm import transition
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProvisioningOutcome:
    """Result of a provision_submission call."""

    success: bool
    submission_id: UUID
    app_id: UUID | None
    error: ProvisioningError | None = None


def _make_default_aca_fn() -> Callable[..., Coroutine[Any, Any, Any]]:
    from rac_control_plane.provisioning import aca
    return aca.create_or_update_app


def _make_default_dns_fn() -> Callable[..., Coroutine[Any, Any, Any]]:
    from rac_control_plane.provisioning import dns
    return dns.upsert_a_record


def _make_default_keys_fn() -> Callable[..., Coroutine[Any, Any, Any]]:
    from rac_control_plane.provisioning import keys
    return keys.create_signing_key


def _make_default_files_fn() -> Callable[..., Coroutine[Any, Any, Any]]:
    from rac_control_plane.provisioning import files
    return files.ensure_app_share


async def _has_existing_signing_key(session: AsyncSession, app_slug: str) -> bool:
    """Return True if a signing key already exists for this app slug."""
    stmt = select(SigningKeyVersion).where(SigningKeyVersion.app_slug == app_slug)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _write_event(
    session: AsyncSession,
    submission_id: UUID,
    kind: str,
    comment: str,
    actor_principal_id: UUID | None = None,
) -> None:
    event = ApprovalEvent(
        submission_id=submission_id,
        kind=kind,
        actor_principal_id=actor_principal_id,
        comment=comment,
    )
    session.add(event)
    await session.flush()


async def provision_submission(
    session: AsyncSession,
    submission: Submission,
    *,
    max_attempts: int = 3,
    aca_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
    dns_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
    keys_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
    files_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
) -> ProvisioningOutcome:
    """Provision all Tier 3 Azure resources for an approved submission.

    Args:
        session: Active async session.
        submission: Submission in 'approved' state.
        max_attempts: Maximum retry count for transient errors.
        aca_fn: Injectable ACA wrapper (for tests).
        dns_fn: Injectable DNS wrapper (for tests).
        keys_fn: Injectable Key Vault wrapper (for tests).
        files_fn: Injectable Azure Files wrapper (for tests).

    Returns:
        ProvisioningOutcome with success flag.
    """
    settings = get_settings()

    if aca_fn is None:
        aca_fn = _make_default_aca_fn()
    if dns_fn is None:
        dns_fn = _make_default_dns_fn()
    if keys_fn is None:
        keys_fn = _make_default_keys_fn()
    if files_fn is None:
        files_fn = _make_default_files_fn()

    # Step 1: Upsert app row
    app = await upsert_app_for_approved_submission(session, submission)
    logger.info(
        "provisioning_started",
        submission_id=str(submission.id),
        slug=submission.slug,
        app_id=str(app.id),
    )

    # Step 2: Build tags
    tags = build_tier3_tags(
        slug=app.slug,
        pi_principal_id=app.pi_principal_id,
        submission_id=submission.id,
        env=settings.env,
    )

    image_ref = f"{settings.acr_login_server}/{app.slug}:{submission.id}"

    attempt = 0
    last_error: ProvisioningError | None = None

    while attempt < max_attempts:
        attempt += 1
        try:
            # Step 3: Ensure Azure Files share
            await files_fn(
                storage_account_name=settings.files_storage_account_name,
                share_name=app.slug,
                tags=tags,
            )
            logger.info("provisioning_files_done", slug=app.slug, attempt=attempt)

            # Step 4: Create signing key (idempotent — only on first deploy)
            key_exists = await _has_existing_signing_key(session, app.slug)
            if not key_exists:
                key_result = await keys_fn(app_slug=app.slug, tags=tags)
                # Store the kid in signing_key_version
                skv = SigningKeyVersion(
                    app_slug=app.slug,
                    kv_kid=key_result.kid,
                    kv_version_id=key_result.version,
                    algorithm="ES256",
                )
                session.add(skv)
                await session.flush()
                logger.info("provisioning_key_created", slug=app.slug, kid=key_result.kid)
            else:
                logger.info("provisioning_key_exists", slug=app.slug)

            # Step 5: Create/update ACA app
            await aca_fn(
                slug=app.slug,
                pi_principal_id=str(app.pi_principal_id),
                submission_id=str(submission.id),
                target_port=app.target_port,
                cpu_cores=float(app.cpu_cores),
                memory_gb=float(app.memory_gb),
                image_ref=image_ref,
                env_vars=[
                    {"name": "RAC_APP_SLUG", "value": app.slug},
                    {"name": "RAC_SUBMISSION_ID", "value": str(submission.id)},
                ],
                azure_files_share_name=app.slug,
                storage_account_name=settings.files_storage_account_name,
                storage_account_key_secret_uri=(
                    f"{settings.kv_uri}/secrets/"
                    f"{settings.files_storage_account_key_kv_secret_name}"
                ),
                tags=tags,
            )
            logger.info("provisioning_aca_done", slug=app.slug, attempt=attempt)

            # Step 6: Upsert DNS A record
            await dns_fn(
                zone_name=settings.dns_zone_name,
                subdomain=app.slug,
                ip_address=settings.app_gateway_public_ip,
                tags=tags,
            )
            logger.info("provisioning_dns_done", slug=app.slug, attempt=attempt)

            # Step 7: Transition submission approved → deployed
            new_status = transition(submission.status, "provisioning_completed")  # type: ignore[arg-type]
            submission.status = new_status  # type: ignore[assignment]
            submission.updated_at = datetime.now(UTC)
            session.add(submission)
            await session.flush()

            # Step 8: Write provisioning_completed event
            await _write_event(
                session,
                submission.id,
                kind="provisioning_completed",
                comment="all steps successful",
            )

            # Step 9: Commit (caller may also commit; flush ensures visibility)
            await session.commit()

            logger.info(
                "provisioning_succeeded",
                slug=app.slug,
                submission_id=str(submission.id),
                attempts=attempt,
            )
            return ProvisioningOutcome(
                success=True,
                submission_id=submission.id,
                app_id=app.id,
            )

        except ProvisioningError as err:
            last_error = err
            decision = decide_retry(err, attempt, max_attempts=max_attempts)
            logger.warning(
                "provisioning_error",
                slug=app.slug,
                code=err.code,
                detail=err.detail,
                retryable=err.retryable,
                attempt=attempt,
                should_retry=decision.should_retry,
                delay_seconds=decision.delay_seconds,
            )

            if decision.should_retry:
                await asyncio.sleep(decision.delay_seconds)
                continue

            # Permanent failure or retries exhausted
            break

    # Out of retries or permanent error
    if last_error is None:
        raise RuntimeError("provision_submission: exited retry loop without capturing an error")

    await _write_event(
        session,
        submission.id,
        kind="provisioning_failed",
        comment=f"{last_error.code}: {last_error.detail}",
    )
    # Submission stays at 'approved' — do NOT call provisioning_failed FSM transition
    # (it would loop back to approved anyway, but keeping explicit is cleaner)
    await session.commit()

    logger.error(
        "provisioning_failed_permanent",
        slug=submission.slug,
        submission_id=str(submission.id),
        code=last_error.code,
        detail=last_error.detail,
        attempts=attempt,
    )
    return ProvisioningOutcome(
        success=False,
        submission_id=submission.id,
        app_id=app.id,
        error=last_error,
    )
