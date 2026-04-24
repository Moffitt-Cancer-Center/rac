"""Integration tests for the detection engine (Task 6).

Tests run against a real Postgres testcontainer with full migrations.
All scenarios from the plan spec:
1. Interactive user + 0 findings → state stays awaiting_scan
2. Interactive user + 1 warn finding → awaiting_scan, finding row exists
3. Agent + 1 warn finding → needs_user_action (AC4.5)
4. Interactive user + 1 error finding → needs_user_action
5. Same rule fires twice on same submission → 2 detection_finding rows (AC4.6)
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import DetectionFinding, Submission, SubmissionStatus
from rac_control_plane.detection.contracts import Finding, RepoContext, RepoFile, Rule
from rac_control_plane.detection.engine import run_detection


# ---------------------------------------------------------------------------
# Helper: build a Submission ORM row
# ---------------------------------------------------------------------------

def _make_submission(
    session: AsyncSession,
    *,
    submitter_principal_id: UUID | None = None,
    status: SubmissionStatus = SubmissionStatus.awaiting_scan,
) -> Submission:
    sub = Submission(
        slug=f"test-{uuid4().hex[:8]}",
        status=status,
        submitter_principal_id=submitter_principal_id or uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
    )
    session.add(sub)
    return sub


def _make_prebuilt_repo(tmp_path: Path, *, has_dockerfile: bool = True) -> Path:
    """Create a minimal repo directory for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if has_dockerfile:
        (repo / "Dockerfile").write_text("FROM python:3.12\nRUN echo hello\n")
    (repo / "app.py").write_text("print('hello')\n")
    return repo


# ---------------------------------------------------------------------------
# Rule factories for tests
# ---------------------------------------------------------------------------

def _make_rule(rule_id: str, findings: list[Finding]) -> Rule:
    captured = list(findings)

    def evaluate(ctx: RepoContext) -> list[Finding]:
        return captured

    return Rule(rule_id=rule_id, version=1, default_severity="warn", evaluate=evaluate)


def _warn_finding(rule_id: str) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_version=1,
        severity="warn",
        title="Test warn",
        detail="Test detail",
    )


def _error_finding(rule_id: str) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_version=1,
        severity="error",
        title="Test error",
        detail="Test error detail",
    )


# ---------------------------------------------------------------------------
# Scenario 1: Interactive user + 0 findings → awaiting_scan, no findings row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interactive_user_zero_findings_stays_awaiting_scan(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Interactive user + 0 findings → state stays awaiting_scan."""
    sub = _make_submission(db_session)
    await db_session.flush()

    repo = _make_prebuilt_repo(tmp_path)
    no_findings_rule = _make_rule("test/silent", [])

    inserted = await run_detection(
        db_session,
        sub,
        rules={"test/silent": no_findings_rule},
        principal_kind="user",
        _prebuilt_repo_root=repo,
    )

    assert inserted == []
    assert sub.status == SubmissionStatus.awaiting_scan


# ---------------------------------------------------------------------------
# Scenario 2: Interactive user + 1 warn finding → awaiting_scan, finding exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interactive_user_warn_finding_stays_awaiting_scan(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Interactive user + 1 warn finding → awaiting_scan (only error blocks), finding row exists."""
    sub = _make_submission(db_session)
    await db_session.flush()

    repo = _make_prebuilt_repo(tmp_path)
    warn_rule = _make_rule("test/warn", [_warn_finding("test/warn")])

    inserted = await run_detection(
        db_session,
        sub,
        rules={"test/warn": warn_rule},
        principal_kind="user",
        _prebuilt_repo_root=repo,
    )

    assert len(inserted) == 1
    assert sub.status == SubmissionStatus.awaiting_scan

    # Verify finding row in DB
    result = await db_session.execute(
        select(DetectionFinding).where(DetectionFinding.submission_id == sub.id)
    )
    db_findings = list(result.scalars().all())
    assert len(db_findings) == 1
    assert db_findings[0].rule_id == "test/warn"
    assert db_findings[0].severity == "warn"


# ---------------------------------------------------------------------------
# Scenario 3: Agent + 1 warn finding → needs_user_action (AC4.5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_warn_finding_transitions_to_needs_user_action(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """AC4.5: Agent + 1 warn finding → needs_user_action."""
    sub = _make_submission(db_session)
    await db_session.flush()

    repo = _make_prebuilt_repo(tmp_path)
    warn_rule = _make_rule("test/warn", [_warn_finding("test/warn")])

    inserted = await run_detection(
        db_session,
        sub,
        rules={"test/warn": warn_rule},
        principal_kind="agent",
        _prebuilt_repo_root=repo,
    )

    assert len(inserted) == 1
    assert sub.status == SubmissionStatus.needs_user_action


# ---------------------------------------------------------------------------
# Scenario 4: Interactive user + 1 error finding → needs_user_action
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interactive_user_error_finding_transitions_to_needs_user_action(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Interactive user + 1 error finding → needs_user_action."""
    sub = _make_submission(db_session)
    await db_session.flush()

    repo = _make_prebuilt_repo(tmp_path)
    error_rule = _make_rule("test/error", [_error_finding("test/error")])

    inserted = await run_detection(
        db_session,
        sub,
        rules={"test/error": error_rule},
        principal_kind="user",
        _prebuilt_repo_root=repo,
    )

    assert len(inserted) == 1
    assert sub.status == SubmissionStatus.needs_user_action


# ---------------------------------------------------------------------------
# Scenario 5: Same rule fires twice → 2 detection_finding rows (AC4.6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_rule_fires_twice_produces_two_rows(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """AC4.6: Same rule emitting 2 findings → 2 distinct detection_finding rows."""
    sub = _make_submission(db_session)
    await db_session.flush()

    repo = _make_prebuilt_repo(tmp_path)

    f1 = Finding(
        rule_id="test/multi",
        rule_version=1,
        severity="warn",
        title="Multi finding 1",
        detail="Detail 1",
        line_ranges=((2, 2),),
    )
    f2 = Finding(
        rule_id="test/multi",
        rule_version=1,
        severity="warn",
        title="Multi finding 2",
        detail="Detail 2",
        line_ranges=((5, 5),),
    )
    multi_rule = _make_rule("test/multi", [f1, f2])

    inserted = await run_detection(
        db_session,
        sub,
        rules={"test/multi": multi_rule},
        principal_kind="user",
        _prebuilt_repo_root=repo,
    )

    assert len(inserted) == 2

    # Verify in DB
    result = await db_session.execute(
        select(DetectionFinding).where(DetectionFinding.submission_id == sub.id)
    )
    db_findings = list(result.scalars().all())
    assert len(db_findings) == 2


# ---------------------------------------------------------------------------
# Scenario 6: RepoContextError from missing Dockerfile propagates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_dockerfile_raises_repo_context_error(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Missing Dockerfile in the prebuilt repo → RepoContextError propagates."""
    from rac_control_plane.detection.repo_context import RepoContextError

    sub = _make_submission(db_session)
    await db_session.flush()

    # Create a repo without a Dockerfile
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')")

    silent_rule = _make_rule("test/silent", [])

    with pytest.raises(RepoContextError):
        await run_detection(
            db_session,
            sub,
            rules={"test/silent": silent_rule},
            principal_kind="user",
            _prebuilt_repo_root=repo,
        )
