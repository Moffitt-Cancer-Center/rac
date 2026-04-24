"""Integration tests for the Tier 3 provisioning orchestrator.

Uses a real Postgres testcontainer. All Azure SDK calls are injected via
aca_fn, dns_fn, keys_fn, files_fn kwargs — no real Azure calls made.

Uses orch_session (a committable session, not SAVEPOINT-wrapped) because
provision_submission calls session.commit() internally.

Verifies:
- AC6.1: submission reaches 'deployed' state, app row updated.
- AC6.3: permanent error keeps submission at 'approved', writes failed event.
- AC6.4: re-submission updates app.current_submission_id atomically.
- AC11.1: all 4 required tags passed to every wrapper.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rac_control_plane.data.models import (
    App,
    ApprovalEvent,
    SigningKeyVersion,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError
from rac_control_plane.provisioning.keys import KeyIdentifier
from rac_control_plane.services.provisioning.orchestrator import (
    ProvisioningOutcome,
    provision_submission,
)
from tests.conftest_settings_helper import make_test_settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_settings in the orchestrator module."""
    settings = make_test_settings()
    monkeypatch.setattr(
        "rac_control_plane.services.provisioning.orchestrator.get_settings",
        lambda: settings,
    )


@pytest.fixture
async def orch_session(migrated_db: str):  # type: ignore[no-untyped-def]
    """Committable session for orchestrator tests.

    provision_submission calls session.commit() internally, so tests cannot use
    the SAVEPOINT-wrapped db_session fixture. Each test uses unique IDs for
    data isolation.
    """
    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


async def _make_submission(
    db_setup: AsyncSession,
    *,
    slug: str | None = None,
    status: SubmissionStatus = SubmissionStatus.approved,
) -> Submission:
    slug = slug or f"test-{uuid4().hex[:8]}"
    sub = Submission(
        slug=slug,
        status=status,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=uuid4(),
        dept_fallback="Test Dept",
    )
    db_setup.add(sub)
    await db_setup.commit()
    return sub


def _noop_files_fn() -> AsyncMock:
    return AsyncMock(return_value="/sub/rg/storage/share")


def _noop_keys_fn() -> AsyncMock:
    return AsyncMock(
        return_value=KeyIdentifier(
            kid="https://kv.vault.azure.net/keys/rac-app-test-v1/ver1",
            key_name="rac-app-test-v1",
            version="ver1",
        )
    )


def _noop_aca_fn() -> AsyncMock:
    return AsyncMock(return_value={"fqdn": "test.internal", "revision_name": "rev1", "ingress_type": "internal"})


def _noop_dns_fn() -> AsyncMock:
    return AsyncMock(return_value="/sub/rg/dns/A/testapp")


async def _fresh_session(migrated_db: str) -> AsyncSession:
    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return await sm().__aenter__()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(orch_session: AsyncSession, db_setup: AsyncSession, migrated_db: str) -> None:
    """AC6.1: submission reaches 'deployed', app.current_submission_id set, event written."""
    sub = await _make_submission(db_setup)

    result = await orch_session.execute(select(Submission).where(Submission.id == sub.id))
    sub_in = result.scalar_one()

    outcome = await provision_submission(
        orch_session,
        sub_in,
        aca_fn=_noop_aca_fn(),
        dns_fn=_noop_dns_fn(),
        keys_fn=_noop_keys_fn(),
        files_fn=_noop_files_fn(),
    )

    assert outcome.success is True
    assert outcome.submission_id == sub.id
    assert outcome.error is None

    # Verify in a fresh session
    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as verify_session:
        sub_check = (await verify_session.execute(
            select(Submission).where(Submission.id == sub.id)
        )).scalar_one()
        assert sub_check.status == SubmissionStatus.deployed

        app_check = (await verify_session.execute(
            select(App).where(App.slug == sub.slug)
        )).scalar_one()
        assert app_check.current_submission_id == sub.id

        event_check = (await verify_session.execute(
            select(ApprovalEvent)
            .where(ApprovalEvent.submission_id == sub.id)
            .where(ApprovalEvent.kind == "provisioning_completed")
        )).scalar_one_or_none()
        assert event_check is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_re_submission_updates_current_submission_atomically(
    db_setup: AsyncSession,
    migrated_db: str,
) -> None:
    """AC6.4: second submission for same slug atomically updates current_submission_id."""
    slug = f"re-sub-{uuid4().hex[:8]}"

    sub1 = await _make_submission(db_setup, slug=slug)

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # First provision
    async with sm() as session1:
        s1 = (await session1.execute(select(Submission).where(Submission.id == sub1.id))).scalar_one()
        await provision_submission(session1, s1, aca_fn=_noop_aca_fn(), dns_fn=_noop_dns_fn(),
                                   keys_fn=_noop_keys_fn(), files_fn=_noop_files_fn())

    # Second submission
    sub2 = await _make_submission(db_setup, slug=slug)
    async with sm() as session2:
        s2 = (await session2.execute(select(Submission).where(Submission.id == sub2.id))).scalar_one()
        outcome2 = await provision_submission(session2, s2, aca_fn=_noop_aca_fn(), dns_fn=_noop_dns_fn(),
                                              keys_fn=_noop_keys_fn(), files_fn=_noop_files_fn())

    assert outcome2.success is True

    # App points to sub2
    async with sm() as verify:
        app = (await verify.execute(select(App).where(App.slug == slug))).scalar_one()
        assert app.current_submission_id == sub2.id

        # sub1 row still exists
        sub1_row = (await verify.execute(select(Submission).where(Submission.id == sub1.id))).scalar_one()
        assert sub1_row.id == sub1.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_dns_conflict_stays_approved(
    orch_session: AsyncSession,
    db_setup: AsyncSession,
    migrated_db: str,
) -> None:
    """AC6.3: permanent DNS error keeps submission at approved, writes failed event."""
    sub = await _make_submission(db_setup)

    result = await orch_session.execute(select(Submission).where(Submission.id == sub.id))
    sub_in = result.scalar_one()

    dns_fn = AsyncMock(
        side_effect=ProvisioningError(code="dns_conflict", detail="conflict", retryable=False)
    )

    outcome = await provision_submission(
        orch_session,
        sub_in,
        aca_fn=_noop_aca_fn(),
        dns_fn=dns_fn,
        keys_fn=_noop_keys_fn(),
        files_fn=_noop_files_fn(),
    )

    assert outcome.success is False
    assert outcome.error is not None
    assert outcome.error.code == "dns_conflict"

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as verify:
        sub_check = (await verify.execute(select(Submission).where(Submission.id == sub.id))).scalar_one()
        assert sub_check.status == SubmissionStatus.approved

        ev = (await verify.execute(
            select(ApprovalEvent)
            .where(ApprovalEvent.submission_id == sub.id)
            .where(ApprovalEvent.kind == "provisioning_failed")
        )).scalar_one_or_none()
        assert ev is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_aca_transient_retries_succeed(
    db_setup: AsyncSession,
    migrated_db: str,
) -> None:
    """ACA raises TransientProvisioningError twice, then succeeds → outcome.success=True."""
    sub = await _make_submission(db_setup)

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    call_count = 0

    async def flaky_aca(*args: object, **kwargs: object) -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TransientProvisioningError(code="aca_transient", detail="503")
        return {"fqdn": "ok", "revision_name": "rev1", "ingress_type": "internal"}

    import unittest.mock as mock

    async def fast_sleep(delay: float) -> None:
        pass

    async with sm() as session:
        s = (await session.execute(select(Submission).where(Submission.id == sub.id))).scalar_one()
        with mock.patch("asyncio.sleep", side_effect=fast_sleep):
            outcome = await provision_submission(
                session, s,
                aca_fn=flaky_aca,
                dns_fn=_noop_dns_fn(),
                keys_fn=_noop_keys_fn(),
                files_fn=_noop_files_fn(),
                max_attempts=3,
            )

    await engine.dispose()

    assert outcome.success is True
    assert call_count == 3


@pytest.mark.asyncio
async def test_retries_exhausted(
    db_setup: AsyncSession,
    migrated_db: str,
) -> None:
    """ACA raises TransientProvisioningError 3 times → outcome.success=False."""
    sub = await _make_submission(db_setup)

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    transient = TransientProvisioningError(code="aca_transient", detail="503")
    aca_fn = AsyncMock(side_effect=transient)

    import unittest.mock as mock

    async def fast_sleep(delay: float) -> None:
        pass

    async with sm() as session:
        s = (await session.execute(select(Submission).where(Submission.id == sub.id))).scalar_one()
        with mock.patch("asyncio.sleep", side_effect=fast_sleep):
            outcome = await provision_submission(
                session, s,
                aca_fn=aca_fn,
                dns_fn=_noop_dns_fn(),
                keys_fn=_noop_keys_fn(),
                files_fn=_noop_files_fn(),
                max_attempts=3,
            )

    await engine.dispose()

    assert outcome.success is False
    assert outcome.error is not None
    assert outcome.error.code == "aca_transient"
    assert aca_fn.call_count == 3


@pytest.mark.asyncio
async def test_tag_assertion(
    db_setup: AsyncSession,
    migrated_db: str,
) -> None:
    """AC11.1: all 4 required tags present in every wrapper call."""
    sub = await _make_submission(db_setup)

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    captured_tags: list[dict[str, str]] = []

    async def capturing_aca(*args: object, **kwargs: object) -> dict[str, str]:
        captured_tags.append(kwargs.get("tags", {}))  # type: ignore[arg-type]
        return {"fqdn": "ok", "revision_name": "r1", "ingress_type": "internal"}

    async def capturing_dns(*args: object, **kwargs: object) -> str:
        captured_tags.append(kwargs.get("tags", {}))  # type: ignore[arg-type]
        return "/rg/dns/A/app"

    async def capturing_keys(*args: object, **kwargs: object) -> KeyIdentifier:
        captured_tags.append(kwargs.get("tags", {}))  # type: ignore[arg-type]
        return KeyIdentifier(kid="https://kv/k/v", key_name="k", version="v")

    async def capturing_files(*args: object, **kwargs: object) -> str:
        captured_tags.append(kwargs.get("tags", {}))  # type: ignore[arg-type]
        return "/rg/storage/share"

    async with sm() as session:
        s = (await session.execute(select(Submission).where(Submission.id == sub.id))).scalar_one()
        outcome = await provision_submission(
            session, s,
            aca_fn=capturing_aca,
            dns_fn=capturing_dns,
            keys_fn=capturing_keys,
            files_fn=capturing_files,
        )

    await engine.dispose()

    assert outcome.success is True
    assert len(captured_tags) >= 4

    required_keys = {"rac_env", "rac_app_slug", "rac_pi_principal_id", "rac_submission_id"}
    for tag_dict in captured_tags:
        for k in required_keys:
            assert k in tag_dict, f"Required tag {k!r} missing from wrapper call"


@pytest.mark.asyncio
async def test_idempotent_key_creation(
    db_setup: AsyncSession,
    migrated_db: str,
) -> None:
    """AC6.4: second provision run for same app → keys_fn NOT called again."""
    slug = f"idem-{uuid4().hex[:8]}"

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    sub1 = await _make_submission(db_setup, slug=slug)
    keys_fn1 = AsyncMock(return_value=KeyIdentifier(kid="https://kv/k/v", key_name="k", version="v"))

    async with sm() as session:
        s1 = (await session.execute(select(Submission).where(Submission.id == sub1.id))).scalar_one()
        await provision_submission(session, s1, aca_fn=_noop_aca_fn(), dns_fn=_noop_dns_fn(),
                                   keys_fn=keys_fn1, files_fn=_noop_files_fn())

    assert keys_fn1.call_count == 1

    sub2 = await _make_submission(db_setup, slug=slug)
    keys_fn2 = AsyncMock(return_value=KeyIdentifier(kid="https://kv/k/v2", key_name="k", version="v2"))

    async with sm() as session:
        s2 = (await session.execute(select(Submission).where(Submission.id == sub2.id))).scalar_one()
        await provision_submission(session, s2, aca_fn=_noop_aca_fn(), dns_fn=_noop_dns_fn(),
                                   keys_fn=keys_fn2, files_fn=_noop_files_fn())

    await engine.dispose()

    # keys_fn2 should NOT have been called (key already exists for this slug)
    assert keys_fn2.call_count == 0
