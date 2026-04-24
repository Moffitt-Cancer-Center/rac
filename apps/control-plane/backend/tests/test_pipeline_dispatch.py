"""Tests for the pipeline dispatch service (Task 7).

Tests:
1. test_build_dispatch_payload_shape           — pure payload builder
2. test_mint_callback_secret_stores_with_expiry — Key Vault mock
3. test_dispatch_success                        — 204 from GitHub
4. test_dispatch_404_raises                     — 4xx → PipelineDispatchError
5. test_dispatch_5xx_retries_then_succeeds      — 500 then 204
6. test_dispatch_5xx_exhausts_retries           — 500 × N → PipelineDispatchError
7. test_dispatch_payload_too_large_raises       — size check → ValidationApiError
8. test_create_submission_dispatches            — integration: POST /submissions
9. test_create_submission_on_dispatch_422_fails — 422 from GitHub → pipeline_error
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import respx
from httpx import Response
from sqlalchemy import select

from rac_control_plane.data.models import Submission, SubmissionStatus
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.pipeline_dispatch.github import (
    MAX_PAYLOAD_BYTES,
    PipelineDispatchError,
    dispatch,
)
from rac_control_plane.services.pipeline_dispatch.payload import build_dispatch_payload
from rac_control_plane.services.pipeline_dispatch.secret_mint import mint_callback_secret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_submission(**kwargs: Any) -> Submission:
    """Build a Submission-like object with the fields payload.py needs.

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


_GH_DISPATCH_URL = "https://api.github.com/repos/test-org/rac-pipeline/dispatches"

_GITHUB_REPO_URL_FOR_SUBMISSIONS = "https://api.github.com/repos/testowner/testrepo"
_GITHUB_DOCKERFILE_URL = (
    "https://api.github.com/repos/testowner/testrepo/contents/Dockerfile"
)


def _mock_github_repo_success() -> respx.MockRouter:
    """Mock the GitHub repo validation calls that happen in create_submission."""
    router = respx.MockRouter(assert_all_called=False)
    router.get(_GITHUB_REPO_URL_FOR_SUBMISSIONS).mock(
        return_value=Response(
            200, json={"id": 1, "name": "testrepo", "full_name": "testowner/testrepo"}
        )
    )
    router.get(_GITHUB_DOCKERFILE_URL, params={"ref": "main"}).mock(
        return_value=Response(200, json={"name": "Dockerfile", "path": "Dockerfile"})
    )
    return router


# ---------------------------------------------------------------------------
# Test 1 — Pure payload builder
# ---------------------------------------------------------------------------

def test_build_dispatch_payload_shape() -> None:
    """build_dispatch_payload returns the exact dict shape the workflow expects."""
    sub_id = uuid4()
    submission = _make_submission(
        id=sub_id,
        slug="my-app-abc",
        github_repo_url="https://github.com/org/repo",
        git_ref="v1.2.3",
        dockerfile_path="docker/Dockerfile",
    )

    payload = build_dispatch_payload(
        submission,
        callback_base_url="https://cp.rac.example.org",
        callback_secret_name="rac-pipeline-cb-" + str(sub_id),
    )

    assert payload["submission_id"] == str(sub_id)
    assert payload["repo_url"] == "https://github.com/org/repo"
    assert payload["git_ref"] == "v1.2.3"
    assert payload["dockerfile_path"] == "docker/Dockerfile"
    assert payload["slug"] == "my-app-abc"
    assert str(sub_id) in payload["callback_url"], (
        f"callback_url must contain submission UUID; got {payload['callback_url']!r}"
    )
    assert "https://cp.rac.example.org" in payload["callback_url"]
    assert payload["callback_secret_name"] == f"rac-pipeline-cb-{sub_id}"

    # All expected keys are present
    expected_keys = {
        "submission_id", "repo_url", "git_ref",
        "dockerfile_path", "slug", "callback_url", "callback_secret_name",
    }
    assert set(payload.keys()) == expected_keys


def test_build_dispatch_payload_callback_url_format() -> None:
    """callback_url is /webhooks/pipeline-callback/{submission_id}."""
    sub_id = uuid4()
    submission = _make_submission(id=sub_id)
    payload = build_dispatch_payload(
        submission,
        callback_base_url="https://cp.rac.example.org/",  # trailing slash handled
        callback_secret_name="s",
    )
    expected_url = f"https://cp.rac.example.org/webhooks/pipeline-callback/{sub_id}"
    assert payload["callback_url"] == expected_url, (
        f"Expected {expected_url!r}, got {payload['callback_url']!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — mint_callback_secret stores with correct expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mint_callback_secret_stores_with_expiry() -> None:
    """mint_callback_secret calls set_secret with correct name, hex value, and expiry."""
    sub_id = uuid4()
    expiry_minutes = 240

    mock_client = AsyncMock()
    mock_client.set_secret = AsyncMock(return_value=None)

    before = datetime.now(tz=timezone.utc)
    secret_name, secret_value = await mint_callback_secret(
        sub_id,
        kv_uri="https://test-kv.vault.azure.net/",
        expiry_minutes=expiry_minutes,
        client=mock_client,
    )
    after = datetime.now(tz=timezone.utc)

    # Name has correct format
    assert secret_name == f"rac-pipeline-cb-{sub_id}"

    # Value is 64 hex chars (32 bytes)
    assert len(secret_value) == 64
    assert all(c in "0123456789abcdef" for c in secret_value), (
        f"secret_value is not valid hex: {secret_value!r}"
    )

    # set_secret was called once
    mock_client.set_secret.assert_called_once()
    call_kwargs = mock_client.set_secret.call_args

    # First positional arg is the secret name
    call_name = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("name")
    assert call_name == secret_name

    # Second positional arg is the value
    call_value = call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("value")
    assert call_value == secret_value

    # expires_on kwarg is within the expected window
    expires_on: datetime = call_kwargs.kwargs.get("expires_on")
    assert expires_on is not None, "expires_on must be passed to set_secret"
    min_expiry = before + timedelta(minutes=expiry_minutes - 1)
    max_expiry = after + timedelta(minutes=expiry_minutes + 1)
    assert min_expiry <= expires_on <= max_expiry, (
        f"expires_on={expires_on} not in [{min_expiry}, {max_expiry}]"
    )


# ---------------------------------------------------------------------------
# Test 3 — dispatch: 204 success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_success() -> None:
    """dispatch() succeeds silently when GitHub returns 204."""
    payload: dict[str, Any] = {
        "submission_id": str(uuid4()),
        "repo_url": "https://github.com/org/repo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "slug": "test-slug",
        "callback_url": "https://cp.example.org/webhooks/pipeline-callback/123",
        "callback_secret_name": "rac-pipeline-cb-123",
    }

    # Capture request body inside the mock context (calls are available within the block)
    captured_body: dict[str, Any] = {}

    with respx.mock(assert_all_called=True) as mock:
        mock.post(_GH_DISPATCH_URL).mock(return_value=Response(204))
        await dispatch(
            "test-org",
            "rac-pipeline",
            payload,
            auth_token="ghp_test",
        )
        # Verify request body contains event_type and client_payload
        assert len(mock.calls) == 1
        request = mock.calls[0].request
        captured_body = json.loads(request.content)

    assert captured_body["event_type"] == "rac_submission"
    assert captured_body["client_payload"] == payload


# ---------------------------------------------------------------------------
# Test 4 — dispatch: 404 raises PipelineDispatchError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_404_raises() -> None:
    """dispatch() raises PipelineDispatchError on 4xx response."""
    payload: dict[str, Any] = {"submission_id": str(uuid4())}

    with respx.mock:
        respx.post(_GH_DISPATCH_URL).mock(return_value=Response(404))
        with pytest.raises(PipelineDispatchError) as exc_info:
            await dispatch("test-org", "rac-pipeline", payload, auth_token="ghp_test")

    assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# Test 5 — dispatch: 500 then 204 (retry succeeds)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_5xx_retries_then_succeeds() -> None:
    """dispatch() retries on 5xx and succeeds on the second attempt."""
    payload: dict[str, Any] = {"submission_id": str(uuid4())}

    with respx.mock(assert_all_called=True) as mock:
        mock.post(_GH_DISPATCH_URL).mock(
            side_effect=[Response(500), Response(204)]
        )
        # Patch sleep to avoid actual delays in tests
        with patch("rac_control_plane.services.pipeline_dispatch.github.asyncio.sleep"):
            await dispatch("test-org", "rac-pipeline", payload, auth_token="ghp_test")

        # Assert call count inside the context block (calls cleared on exit)
        assert len(mock.calls) == 2


# ---------------------------------------------------------------------------
# Test 6 — dispatch: retry exhaustion raises PipelineDispatchError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_5xx_exhausts_retries() -> None:
    """dispatch() raises PipelineDispatchError after max_retries=3 all return 500."""
    payload: dict[str, Any] = {"submission_id": str(uuid4())}

    # Provide 5 responses so the mock doesn't run out (max_retries=3 → 3 calls)
    responses = [Response(500)] * 5

    call_count = 0
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_GH_DISPATCH_URL).mock(side_effect=responses)
        with patch("rac_control_plane.services.pipeline_dispatch.github.asyncio.sleep"):
            with pytest.raises(PipelineDispatchError):
                await dispatch("test-org", "rac-pipeline", payload, auth_token="ghp_test")

        # Count calls inside the block
        call_count = len(mock.calls)

    # Exactly 3 attempts (max_retries)
    assert call_count == 3, f"Expected 3 retry attempts, got {call_count}"


# ---------------------------------------------------------------------------
# Test 7 — dispatch: payload too large raises ValidationApiError (no HTTP call)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_payload_too_large_raises() -> None:
    """dispatch() raises ValidationApiError before any HTTP call when payload > 10 KB."""
    # Build a payload that exceeds MAX_PAYLOAD_BYTES
    huge_payload: dict[str, Any] = {
        "submission_id": str(uuid4()),
        "manifest": {"huge": "x" * 15_000},
    }
    payload_size = len(json.dumps(huge_payload).encode("utf-8"))
    assert payload_size > MAX_PAYLOAD_BYTES, (
        f"Test setup error: payload must exceed {MAX_PAYLOAD_BYTES} bytes, got {payload_size}"
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(_GH_DISPATCH_URL).mock(return_value=Response(204))
        with pytest.raises(ValidationApiError) as exc_info:
            await dispatch(
                "test-org", "rac-pipeline", huge_payload, auth_token="ghp_test"
            )

    # No HTTP call was attempted
    assert len(mock.calls) == 0

    # Error code matches spec
    assert exc_info.value.code == "pipeline_payload_too_large"


# ---------------------------------------------------------------------------
# Test 8 — Integration: POST /submissions triggers dispatch as background task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_submission_dispatches(client, db_session, mock_oidc) -> None:
    """POST /submissions → 201; dispatch_fn is called with correct client_payload."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    dispatch_calls: list[dict[str, Any]] = []

    async def _fake_dispatch(payload: dict[str, Any]) -> None:
        dispatch_calls.append(payload)

    body = {
        "github_repo_url": "https://github.com/testowner/testrepo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "paper_title": "Integration Dispatch Test",
        "pi_principal_id": str(uuid4()),
        "dept_fallback": "Bioinformatics",
    }

    with _mock_github_repo_success() as mock:
        mock.start()
        # Patch _build_dispatch_fn to return our fake coroutine
        with patch(
            "rac_control_plane.api.routes.submissions._build_dispatch_fn",
            return_value=_fake_dispatch,
        ):
            response = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response.status_code == 201, response.text
    data = response.json()
    sub_id = data["id"]

    # dispatch_fn was called (synchronously within the create flow)
    assert len(dispatch_calls) == 1, (
        f"Expected dispatch_fn called once, called {len(dispatch_calls)} times"
    )
    dispatched = dispatch_calls[0]
    assert dispatched["submission_id"] == sub_id, (
        f"Dispatched submission_id={dispatched['submission_id']} != response id={sub_id}"
    )
    assert str(sub_id) in dispatched["callback_url"]
    assert "http://test" in dispatched["callback_url"]  # callback_base_url from test config
    assert dispatched["repo_url"] == "https://github.com/testowner/testrepo"
    assert dispatched["git_ref"] == "main"
    assert dispatched["slug"] == data["slug"]


# ---------------------------------------------------------------------------
# Test 9 — Integration: ValidationApiError from dispatch → 422, status=pipeline_error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_submission_on_dispatch_422_fails_submission(
    client, db_session, db_setup, mock_oidc
) -> None:
    """When dispatch raises ValidationApiError, POST /submissions → 422; DB status=pipeline_error."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    async def _raising_dispatch(payload: dict[str, Any]) -> None:
        raise ValidationApiError(
            code="pipeline_payload_too_large",
            public_message="Payload too large",
        )

    body = {
        "github_repo_url": "https://github.com/testowner/testrepo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "paper_title": "Dispatch Failure Test",
        "pi_principal_id": str(uuid4()),
        "dept_fallback": "Oncology",
    }

    with _mock_github_repo_success() as mock:
        mock.start()
        with patch(
            "rac_control_plane.api.routes.submissions._build_dispatch_fn",
            return_value=_raising_dispatch,
        ):
            response = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response.status_code == 422, response.text
    data = response.json()
    assert data.get("code") == "pipeline_payload_too_large", (
        f"Expected code='pipeline_payload_too_large', got {data}"
    )

    # DB: submission row must exist AND be in pipeline_error status.
    # create_submission commits the pipeline_error state before re-raising
    # ValidationApiError, so this state survives into db_setup's session.
    stmt = select(Submission).where(
        Submission.submitter_principal_id == test_user_oid
    )
    result = await db_setup.scalars(stmt)
    rows = list(result)
    assert len(rows) == 1, (
        f"Expected exactly 1 submission row, got {len(rows)}"
    )
    assert rows[0].status == SubmissionStatus.pipeline_error, (
        f"Expected status=pipeline_error, got {rows[0].status}"
    )
