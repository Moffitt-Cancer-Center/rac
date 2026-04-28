"""Unit tests for create_submission dispatch gating (Critical 2 — Phase 4 review).

Verifies:
- test_agent_with_findings_does_not_dispatch: when detection_fn transitions
  submission to needs_user_action, dispatch_for_submission is NOT called.
- test_interactive_with_no_findings_does_dispatch: clean interactive-user path
  with zero findings still dispatches the pipeline via dispatch_for_submission.
"""

from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.submissions import SubmissionCreateRequest
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import DetectionFinding, Submission, SubmissionStatus
from rac_control_plane.services.submissions.create import create_submission
from rac_control_plane.services.submissions.fsm import transition as fsm_transition


def _make_settings_mock() -> MagicMock:
    """Build a minimal settings mock for create_submission unit tests."""
    m = MagicMock()
    m.callback_base_url = "http://test"
    m.gh_pipeline_owner = "test-org"
    m.gh_pipeline_repo = "rac-pipeline"
    m.gh_pat = MagicMock()
    m.gh_pat.get_secret_value = MagicMock(return_value="ghp_test")
    m.pipeline_kv_uri = "https://kv.vault.azure.net/"
    m.pipeline_timeout_minutes = 120
    return m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_principal(kind: str = "user") -> Principal:
    return Principal(
        oid=uuid4(),
        kind=kind,
        display_name="Test User",
        roles=frozenset(),
        agent_id=None,
    )


def _make_request() -> SubmissionCreateRequest:
    return SubmissionCreateRequest(
        github_repo_url="https://github.com/test/repo",  # type: ignore[arg-type]
        git_ref="main",
        dockerfile_path="Dockerfile",
        paper_title="Test Paper",
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
    )


# ---------------------------------------------------------------------------
# Test 1: Agent + findings → needs_user_action → dispatch NOT called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_with_findings_does_not_dispatch(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critical 2: detection transitions to needs_user_action → dispatch skipped.

    The detection_fn transitions the submission to needs_user_action.
    The dispatch_for_submission must NOT be called because the submission is no
    longer in awaiting_scan at Step 7.
    """
    mock_dispatch = AsyncMock()
    monkeypatch.setattr(
        "rac_control_plane.services.submissions.create.dispatch_for_submission",
        mock_dispatch,
    )

    async def _fake_detection_fn(
        session: AsyncSession, submission: Submission
    ) -> list[DetectionFinding]:
        """Simulate agent detection: warn finding → needs_user_action transition."""
        from rac_control_plane.services.submissions.fsm import SubmissionStatus as FsmStatus
        from rac_control_plane.data.models import ApprovalEvent

        finding = DetectionFinding(
            submission_id=submission.id,
            rule_id="test/warn",
            rule_version=1,
            severity="warn",
            title="Warn finding",
            detail="Test",
        )
        session.add(finding)
        await session.flush()

        new_status = fsm_transition(FsmStatus(submission.status), "detection_needs_user_action")
        submission.status = new_status  # type: ignore[assignment]
        session.add(submission)
        await session.flush()

        event = ApprovalEvent(
            submission_id=submission.id,
            kind="detection_needs_user_action",
            actor_principal_id=submission.submitter_principal_id,
        )
        session.add(event)
        await session.flush()
        return [finding]

    # Mock GitHub validation so it passes without network
    import respx
    from httpx import Response
    with (
        patch("rac_control_plane.settings.get_settings", return_value=_make_settings_mock()),
        respx.mock(assert_all_called=False) as mock,
    ):
        mock.get("https://api.github.com/repos/test/repo").mock(
            return_value=Response(200, json={"id": 1})
        )
        mock.get("https://api.github.com/repos/test/repo/contents/Dockerfile").mock(
            return_value=Response(200, json={"name": "Dockerfile"})
        )

        submission = await create_submission(
            db_session,
            _make_principal(kind="agent"),
            _make_request(),
            existing_slugs=set(),
            settings=_make_settings_mock(),
            detection_fn=_fake_detection_fn,
        )

    assert submission.status == SubmissionStatus.needs_user_action, (
        f"Expected needs_user_action, got {submission.status}"
    )
    mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Interactive user + no findings → awaiting_scan → dispatch IS called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interactive_with_no_findings_does_dispatch(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critical 2: clean interactive-user path (no findings) still dispatches pipeline."""
    mock_dispatch = AsyncMock()
    monkeypatch.setattr(
        "rac_control_plane.services.submissions.create.dispatch_for_submission",
        mock_dispatch,
    )

    async def _no_findings_detection_fn(
        session: AsyncSession, submission: Submission
    ) -> list[DetectionFinding]:
        """No findings: submission stays in awaiting_scan."""
        return []

    import respx
    from httpx import Response
    with (
        patch("rac_control_plane.settings.get_settings", return_value=_make_settings_mock()),
        respx.mock(assert_all_called=False) as mock,
    ):
        mock.get("https://api.github.com/repos/test/repo").mock(
            return_value=Response(200, json={"id": 1})
        )
        mock.get("https://api.github.com/repos/test/repo/contents/Dockerfile").mock(
            return_value=Response(200, json={"name": "Dockerfile"})
        )

        submission = await create_submission(
            db_session,
            _make_principal(kind="user"),
            _make_request(),
            existing_slugs=set(),
            settings=_make_settings_mock(),
            detection_fn=_no_findings_detection_fn,
        )

    assert submission.status == SubmissionStatus.awaiting_scan, (
        f"Expected awaiting_scan, got {submission.status}"
    )
    mock_dispatch.assert_called_once_with(
        submission,
        settings=ANY,
        triggered_by="submission_created",
    )
