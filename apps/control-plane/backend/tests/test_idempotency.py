"""Tests for Idempotency-Key middleware.

Verifies AC3.2: idempotent POST requests with same key return same response
without creating duplicate rows.

Tests:
- Test 8: same key + different body → 422 idempotency_key_reused
- Test 9: no key → two separate rows (opt-out path)

Test isolation note
--------------------
Each test uses a unique user OID so DB assertions are scoped and won't
be confused by rows from other tests.
"""

from uuid import uuid4, UUID

import pytest
import respx
from httpx import Response
from sqlalchemy import select

from rac_control_plane.data.models import Submission


_GITHUB_REPO_URL = "https://api.github.com/repos/testowner/testrepo"
_GITHUB_DOCKERFILE_URL = (
    "https://api.github.com/repos/testowner/testrepo/contents/Dockerfile"
)


def _valid_body(paper_title: str = "Integration Testing With Idempotency") -> dict[str, object]:
    return {
        "github_repo_url": "https://github.com/testowner/testrepo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "paper_title": paper_title,
        "pi_principal_id": str(uuid4()),
        "dept_fallback": "Bioinformatics",
    }


def _mock_github_success() -> respx.MockRouter:
    router = respx.MockRouter(assert_all_called=False)
    router.get(_GITHUB_REPO_URL).mock(
        return_value=Response(
            200, json={"id": 1, "name": "testrepo", "full_name": "testowner/testrepo"}
        )
    )
    router.get(_GITHUB_DOCKERFILE_URL, params={"ref": "main"}).mock(
        return_value=Response(200, json={"name": "Dockerfile", "path": "Dockerfile"})
    )
    return router


# ---------------------------------------------------------------------------
# Test 8 — AC3.2: Same key + different body → 422 idempotency_key_reused
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idempotency_same_key_different_body(client, db_session, mock_oidc):
    """AC3.2: Same Idempotency-Key with a DIFFERENT body returns 422 idempotency_key_reused."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    idempotency_key = str(uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }

    body1 = _valid_body(paper_title="First Paper Title Here")
    body2 = _valid_body(paper_title="Completely Different Second Paper Title")

    with _mock_github_success() as mock:
        mock.start()
        response1 = await client.post("/submissions", json=body1, headers=headers)
        response2 = await client.post("/submissions", json=body2, headers=headers)
        mock.stop()

    assert response1.status_code == 201, f"First request failed: {response1.text}"

    # Second request: same key, different body → must be rejected
    assert response2.status_code == 422, (
        f"Expected 422 for key reuse, got {response2.status_code}: {response2.text}"
    )
    data2 = response2.json()
    assert data2.get("code") == "idempotency_key_reused", (
        f"Expected code='idempotency_key_reused', got: {data2}"
    )

    # Only one submission row for this user (first call succeeded, second was rejected)
    stmt = select(Submission).where(
        Submission.submitter_principal_id == test_user_oid
    )
    result = await db_session.scalars(stmt)
    rows = list(result)
    assert len(rows) == 1, f"Expected exactly 1 submission row, found {len(rows)}"


# ---------------------------------------------------------------------------
# Test 9 — Opt-out: no Idempotency-Key header → two separate rows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idempotency_no_key_creates_new_row_each_time(client, db_session, mock_oidc):
    """No Idempotency-Key header → caller opted out; two POSTs create two distinct rows."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    # Deliberately NO Idempotency-Key header
    headers = {"Authorization": f"Bearer {token}"}
    body = _valid_body()

    with _mock_github_success() as mock:
        mock.start()
        response1 = await client.post("/submissions", json=body, headers=headers)
        response2 = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response1.status_code == 201, f"First request failed: {response1.text}"
    assert response2.status_code == 201, f"Second request failed: {response2.text}"

    id1 = response1.json()["id"]
    id2 = response2.json()["id"]
    assert id1 != id2, (
        "Two requests without Idempotency-Key must produce distinct submission IDs"
    )

    # Exactly two submission rows for this user
    stmt = select(Submission).where(
        Submission.submitter_principal_id == test_user_oid
    )
    result = await db_session.scalars(stmt)
    rows = list(result)
    assert len(rows) == 2, f"Expected 2 submission rows (one per request), found {len(rows)}"
