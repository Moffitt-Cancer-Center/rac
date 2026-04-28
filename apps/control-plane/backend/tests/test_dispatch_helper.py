"""Tests for dispatch_for_submission helper (Task 3 & 4).

Tests the shared dispatch helper that both the create-submission flow and
the admin-retry flow delegate to. The helper mints the per-submission HMAC
callback secret in the dedicated pipeline KV, builds the dispatch payload,
and POSTs repository_dispatch.

Design constraints:
  - DispatchUnavailableError is raised BEFORE any I/O when required config
    is missing; this guarantees no orphan KV secret and no DB row left behind.
  - Callback secrets ALWAYS land in settings.pipeline_kv_uri, never in
    settings.kv_uri (the platform KV); the strict separation is part of the
    pipeline identity blast-radius isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from rac_control_plane.data.models import Submission
from rac_control_plane.services.pipeline_dispatch.dispatch_helper import (
    DispatchUnavailableError,
    dispatch_for_submission,
)


def _make_submission(**kwargs: Any) -> Submission:
    """Build a Submission-like object with the fields the helper needs.

    Uses MagicMock so we don't need a live DB session.
    """
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "slug": "test-slug-abc123",
        "github_repo_url": "https://github.com/testowner/testrepo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "manifest": None,
    }
    defaults.update(kwargs)
    obj = MagicMock(spec=Submission)
    for key, value in defaults.items():
        setattr(obj, key, value)
    return obj


def _make_settings(
    *,
    gh_pat_value: str | None = "ghp_test_token_value",
    pipeline_kv_uri: str = "https://pipeline-kv.vault.azure.net/",
    gh_pipeline_owner: str = "test-org",
    gh_pipeline_repo: str = "rac-pipeline",
    callback_base_url: str = "https://cp.rac.example.org",
    pipeline_timeout_minutes: int = 60,
) -> Any:
    """Build a Settings-like duck for dispatch_for_submission.

    gh_pat is a SecretStr-like object exposing get_secret_value(); pass None
    via gh_pat_value to simulate the unconfigured-PAT case.
    """
    pat: Any
    if gh_pat_value is None:
        pat = None
    else:
        pat = MagicMock()
        pat.get_secret_value = MagicMock(return_value=gh_pat_value)
        pat.__bool__ = MagicMock(return_value=True)

    settings = MagicMock()
    settings.gh_pat = pat
    settings.pipeline_kv_uri = pipeline_kv_uri
    settings.gh_pipeline_owner = gh_pipeline_owner
    settings.gh_pipeline_repo = gh_pipeline_repo
    settings.callback_base_url = callback_base_url
    settings.pipeline_timeout_minutes = pipeline_timeout_minutes
    return settings


# ---------------------------------------------------------------------------
# Test 1: Happy path — mints secret in pipeline KV and dispatches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_for_submission_mints_secret_in_pipeline_kv_then_dispatches() -> None:
    """dispatch_for_submission mints secret in pipeline KV, dispatches with real name."""
    sub_id = uuid4()
    submission = _make_submission(id=sub_id)
    settings = _make_settings(
        gh_pat_value="ghp_xyz",
        pipeline_kv_uri="https://pipeline-kv.vault.azure.net/",
    )

    secret_name_returned = f"rac-pipeline-cb-{sub_id}"
    secret_value_returned = "0" * 64

    with patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.mint_callback_secret",
        new=AsyncMock(return_value=(secret_name_returned, secret_value_returned)),
    ) as mock_mint, patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.gh_dispatch.dispatch",
        new=AsyncMock(),
    ) as mock_dispatch:
        result = await dispatch_for_submission(
            submission,
            settings=settings,
            triggered_by="submission_created",
        )

    # Verify mint_callback_secret was called with pipeline_kv_uri (NOT platform KV)
    mock_mint.assert_awaited_once()
    mint_args = mock_mint.await_args.args
    mint_kwargs = mock_mint.await_args.kwargs
    assert mint_args[0] == sub_id
    assert mint_kwargs["kv_uri"] == "https://pipeline-kv.vault.azure.net/"
    assert mint_kwargs["expiry_minutes"] == 120  # 2 × pipeline_timeout_minutes

    # Verify dispatch was called with the real secret name in the payload
    mock_dispatch.assert_awaited_once()
    d_args = mock_dispatch.await_args.args
    d_kwargs = mock_dispatch.await_args.kwargs
    payload = d_args[2]
    assert payload["callback_secret_name"] == f"rac-pipeline-cb-{sub_id}"
    assert payload["submission_id"] == str(sub_id)
    assert payload["callback_url"].endswith(f"/api/webhooks/pipeline-callback/{sub_id}")

    # Verify return shape
    assert result["submission_id"] == str(sub_id)
    assert result["callback_url"] == payload["callback_url"]
    assert "dispatched_at" in result
    # dispatched_at is ISO 8601 UTC
    try:
        datetime.fromisoformat(result["dispatched_at"])
    except ValueError as e:
        pytest.fail(f"dispatched_at is not ISO 8601: {result['dispatched_at']!r}: {e}")


# ---------------------------------------------------------------------------
# Test 2: Raises DispatchUnavailableError when gh_pat is unset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset() -> None:
    """dispatch_for_submission raises DispatchUnavailableError when gh_pat is None.

    No I/O should occur before the error is raised (no mint, no dispatch).
    """
    submission = _make_submission()
    settings = _make_settings(
        gh_pat_value=None,
        pipeline_kv_uri="https://pipeline-kv.vault.azure.net/",
    )

    with patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.mint_callback_secret",
        new=AsyncMock(),
    ) as mock_mint, patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.gh_dispatch.dispatch",
        new=AsyncMock(),
    ) as mock_dispatch:
        with pytest.raises(DispatchUnavailableError, match="RAC_GH_PAT"):
            await dispatch_for_submission(
                submission,
                settings=settings,
                triggered_by="submission_created",
            )

    # No I/O occurred
    assert mock_mint.await_count == 0
    assert mock_dispatch.await_count == 0


# ---------------------------------------------------------------------------
# Test 3: Raises DispatchUnavailableError when pipeline_kv_uri is empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_for_submission_raises_DispatchUnavailableError_when_pipeline_kv_uri_empty() -> None:
    """dispatch_for_submission raises DispatchUnavailableError when pipeline_kv_uri is empty.

    No I/O should occur before the error is raised (no mint, no dispatch).
    """
    submission = _make_submission()
    settings = _make_settings(
        gh_pat_value="ghp_xyz",
        pipeline_kv_uri="",  # Empty!
    )

    with patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.mint_callback_secret",
        new=AsyncMock(),
    ) as mock_mint, patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.gh_dispatch.dispatch",
        new=AsyncMock(),
    ) as mock_dispatch:
        with pytest.raises(DispatchUnavailableError, match="RAC_PIPELINE_KV_URI"):
            await dispatch_for_submission(
                submission,
                settings=settings,
                triggered_by="submission_created",
            )

    # No I/O occurred
    assert mock_mint.await_count == 0
    assert mock_dispatch.await_count == 0


# ---------------------------------------------------------------------------
# Test 4: Emits pipeline_dispatched log with triggered_by
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_for_submission_emits_pipeline_dispatched_log_with_triggered_by() -> None:
    """dispatch_for_submission emits 'pipeline_dispatched' log with triggered_by tag.

    Tests both triggered_by='submission_created' and triggered_by='admin_retry'.
    Uses mocking to capture logger.info calls.
    """
    sub_id = uuid4()
    submission = _make_submission(id=sub_id)
    settings = _make_settings(gh_pat_value="ghp_xyz")

    secret_name_returned = f"rac-pipeline-cb-{sub_id}"

    # Capture logger.info calls to verify the log line is emitted with correct args
    log_calls: list[tuple[str, dict[str, Any]]] = []

    def capture_info(event: str, **kwargs: Any) -> None:
        """Capture logger.info calls."""
        log_calls.append((event, kwargs))

    with patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.mint_callback_secret",
        new=AsyncMock(return_value=(secret_name_returned, "x" * 64)),
    ), patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.gh_dispatch.dispatch",
        new=AsyncMock(),
    ), patch(
        "rac_control_plane.services.pipeline_dispatch.dispatch_helper.logger.info",
        side_effect=capture_info,
    ):
        # Test with triggered_by='submission_created'
        result_created = await dispatch_for_submission(
            submission,
            settings=settings,
            triggered_by="submission_created",
        )

        # Test with triggered_by='admin_retry'
        result_retry = await dispatch_for_submission(
            submission,
            settings=settings,
            triggered_by="admin_retry",
        )

    # Both should succeed and return the same shape
    assert result_created["submission_id"] == str(sub_id)
    assert result_retry["submission_id"] == str(sub_id)

    # Verify log lines were emitted with correct event name and triggered_by
    assert len(log_calls) >= 2, (
        f"Expected at least 2 logger.info calls, found {len(log_calls)}"
    )

    # First call should have triggered_by='submission_created'
    event_1, kwargs_1 = log_calls[0]
    assert event_1 == "pipeline_dispatched"
    assert kwargs_1["submission_id"] == str(sub_id)
    assert kwargs_1["triggered_by"] == "submission_created"

    # Second call should have triggered_by='admin_retry'
    event_2, kwargs_2 = log_calls[1]
    assert event_2 == "pipeline_dispatched"
    assert kwargs_2["submission_id"] == str(sub_id)
    assert kwargs_2["triggered_by"] == "admin_retry"
