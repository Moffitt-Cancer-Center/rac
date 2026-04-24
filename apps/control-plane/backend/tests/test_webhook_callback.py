"""Tests for the pipeline callback webhook endpoint (Task 8).

Scenarios:
1. valid HMAC + verdict=passed        → 200, awaiting_research_review, ScanResult row
2. valid HMAC + verdict=rejected      → 200, scan_rejected, findings in ScanResult
3. valid HMAC + verdict=build_failed  → 200, pipeline_error, build_log_uri recorded
4. valid HMAC + verdict=partial_passed → 200, awaiting_research_review, defender_timed_out=True
5. invalid HMAC                       → 401, status unchanged, no ScanResult
6. stale timestamp (>5 min)           → 401
7. replay (correct HMAC, stale ts)    → 401
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import select

from rac_control_plane.data.models import ApprovalEvent, ScanResult, Submission, SubmissionStatus
from rac_control_plane.services.webhooks.verify import compute_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_callback_body(
    verdict: str = "passed",
    effective_severity: str = "none",
    findings: list[dict] | None = None,
    build_log_uri: str | None = None,
    defender_timed_out: bool = False,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "effective_severity": effective_severity,
        "findings": findings or [],
        "build_log_uri": build_log_uri,
        "sbom_uri": None,
        "grype_report_uri": None,
        "defender_report_uri": None,
        "image_digest": "sha256:abc123",
        "image_ref": "test.azurecr.io/app:tag",
        "defender_timed_out": defender_timed_out,
    }


def _sign_body(secret: bytes, body: bytes, ts: str | None = None) -> tuple[str, str]:
    """Return (timestamp, signature) for a body."""
    if ts is None:
        ts = datetime.now(tz=UTC).isoformat()
    sig = compute_signature(secret, ts, body)
    return ts, sig


async def _create_submission(db_setup: Any, submission_id: UUID | None = None) -> Submission:
    """Insert a submission in awaiting_scan state; return the model."""
    principal_id = uuid4()
    sub = Submission(
        id=submission_id or uuid4(),
        slug="test-app-abc123",
        status=SubmissionStatus.awaiting_scan,
        submitter_principal_id=principal_id,
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=principal_id,
        dept_fallback="Oncology",
    )
    db_setup.add(sub)
    await db_setup.commit()
    return sub


def _make_kv_factory(secret_value: bytes) -> Any:
    """Return a mock kv_client_factory that returns the given secret."""
    mock_client = AsyncMock()
    mock_secret = AsyncMock()
    mock_secret.value = secret_value.decode()
    mock_client.get_secret = AsyncMock(return_value=mock_secret)
    return lambda: mock_client


# ---------------------------------------------------------------------------
# Scenario 1: valid HMAC + verdict=passed → 200, awaiting_research_review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_hmac_passed(client: AsyncClient, db_setup: Any, db_session: Any) -> None:
    """Valid signature + passed verdict → 200; submission advances to awaiting_research_review."""
    secret = b"test-secret-bytes-for-scenario-1"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    body = json.dumps(_make_callback_body(verdict="passed")).encode()
    ts, sig = _sign_body(secret, body)

    # Metric reader
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=secret),
        ),
        patch(
            "rac_control_plane.api.routes.webhooks._delete_callback_secret",
            new=AsyncMock(),
        ),
        patch("rac_control_plane.api.routes.webhooks.scan_verdict_counter") as mock_counter,
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": ts,
                "X-RAC-Signature-256": sig,
            },
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Expire the session identity map so re-queries hit the DB (the app committed separately)
    await db_setup.commit()
    db_setup.expire_all()

    # Verify submission status in a separate query
    result = await db_setup.execute(select(Submission).where(Submission.id == sub_id))
    updated_sub = result.scalar_one()
    assert updated_sub.status == SubmissionStatus.awaiting_research_review

    # Verify ScanResult row
    sr_result = await db_setup.execute(
        select(ScanResult).where(ScanResult.submission_id == sub_id)
    )
    scan_result = sr_result.scalar_one_or_none()
    assert scan_result is not None
    assert scan_result.verdict == "passed"
    assert scan_result.effective_severity == "none"

    # Verify ApprovalEvent (must query after commit so stale cache is cleared)
    ae_result = await db_setup.execute(
        select(ApprovalEvent).where(ApprovalEvent.submission_id == sub_id)
    )
    events = list(ae_result.scalars())
    assert any(e.kind == "scan_completed" for e in events), (
        f"Expected scan_completed event; got kinds: {[e.kind for e in events]}"
    )

    # Verify metric was emitted
    mock_counter.add.assert_called_once_with(1, {"verdict": "passed"})


# ---------------------------------------------------------------------------
# Scenario 2: valid HMAC + verdict=rejected → 200, scan_rejected, findings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_hmac_rejected(client: AsyncClient, db_setup: Any) -> None:
    """Valid signature + rejected verdict → scan_rejected status; findings stored."""
    secret = b"secret-for-rejected-scenario"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    findings = [{"cve_id": "CVE-2021-44228", "severity": "critical", "package_name": "log4j"}]
    body = json.dumps(
        _make_callback_body(verdict="rejected", effective_severity="critical", findings=findings)
    ).encode()
    ts, sig = _sign_body(secret, body)

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=secret),
        ),
        patch(
            "rac_control_plane.api.routes.webhooks._delete_callback_secret",
            new=AsyncMock(),
        ),
        patch("rac_control_plane.metrics.scan_verdict_counter"),
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": ts,
                "X-RAC-Signature-256": sig,
            },
        )

    assert resp.status_code == 200

    await db_setup.commit()
    db_setup.expire_all()

    result = await db_setup.execute(select(Submission).where(Submission.id == sub_id))
    updated_sub = result.scalar_one()
    assert updated_sub.status == SubmissionStatus.scan_rejected

    sr_result = await db_setup.execute(
        select(ScanResult).where(ScanResult.submission_id == sub_id)
    )
    scan_result = sr_result.scalar_one_or_none()
    assert scan_result is not None
    assert scan_result.verdict == "rejected"
    assert scan_result.findings == findings


# ---------------------------------------------------------------------------
# Scenario 3: valid HMAC + verdict=build_failed → pipeline_error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_hmac_build_failed(client: AsyncClient, db_setup: Any) -> None:
    """Valid signature + build_failed → pipeline_error; build_log_uri recorded."""
    secret = b"secret-for-build-failed"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    log_uri = "https://blob.example.org/logs/build-123.log"
    body = json.dumps(
        _make_callback_body(verdict="build_failed", build_log_uri=log_uri)
    ).encode()
    ts, sig = _sign_body(secret, body)

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=secret),
        ),
        patch(
            "rac_control_plane.api.routes.webhooks._delete_callback_secret",
            new=AsyncMock(),
        ),
        patch("rac_control_plane.metrics.scan_verdict_counter"),
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": ts,
                "X-RAC-Signature-256": sig,
            },
        )

    assert resp.status_code == 200

    await db_setup.commit()
    db_setup.expire_all()

    result = await db_setup.execute(select(Submission).where(Submission.id == sub_id))
    updated_sub = result.scalar_one()
    assert updated_sub.status == SubmissionStatus.pipeline_error

    sr_result = await db_setup.execute(
        select(ScanResult).where(ScanResult.submission_id == sub_id)
    )
    scan_result = sr_result.scalar_one_or_none()
    assert scan_result is not None
    assert scan_result.build_log_uri == log_uri


# ---------------------------------------------------------------------------
# Scenario 4: valid HMAC + verdict=partial_passed + defender_timed_out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_hmac_partial_passed(client: AsyncClient, db_setup: Any) -> None:
    """partial_passed → awaiting_research_review; defender_timed_out=True on ScanResult."""
    secret = b"secret-for-partial-passed"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    body = json.dumps(
        _make_callback_body(verdict="partial_passed", defender_timed_out=True)
    ).encode()
    ts, sig = _sign_body(secret, body)

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=secret),
        ),
        patch(
            "rac_control_plane.api.routes.webhooks._delete_callback_secret",
            new=AsyncMock(),
        ),
        patch("rac_control_plane.metrics.scan_verdict_counter"),
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": ts,
                "X-RAC-Signature-256": sig,
            },
        )

    assert resp.status_code == 200

    await db_setup.commit()
    db_setup.expire_all()

    result = await db_setup.execute(select(Submission).where(Submission.id == sub_id))
    updated_sub = result.scalar_one()
    assert updated_sub.status == SubmissionStatus.awaiting_research_review

    sr_result = await db_setup.execute(
        select(ScanResult).where(ScanResult.submission_id == sub_id)
    )
    scan_result = sr_result.scalar_one_or_none()
    assert scan_result is not None
    assert scan_result.defender_timed_out is True


# ---------------------------------------------------------------------------
# Scenario 5: invalid HMAC → 401, status unchanged, no ScanResult
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_hmac_rejected(client: AsyncClient, db_setup: Any) -> None:
    """Invalid HMAC signature → 401; submission stays awaiting_scan; no ScanResult."""
    real_secret = b"real-secret-value"
    wrong_secret = b"wrong-secret-value"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    body = json.dumps(_make_callback_body()).encode()
    # Sign with wrong secret
    ts, sig = _sign_body(wrong_secret, body)

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=real_secret),
        ),
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": ts,
                "X-RAC-Signature-256": sig,
            },
        )

    assert resp.status_code == 401
    data = resp.json()
    assert data["code"] == "invalid_signature"

    # Submission should be unchanged
    result = await db_setup.execute(select(Submission).where(Submission.id == sub_id))
    updated_sub = result.scalar_one()
    assert updated_sub.status == SubmissionStatus.awaiting_scan

    # No ScanResult created
    sr_result = await db_setup.execute(
        select(ScanResult).where(ScanResult.submission_id == sub_id)
    )
    assert sr_result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Scenario 6: stale timestamp (>5 min) → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_timestamp_rejected(client: AsyncClient, db_setup: Any) -> None:
    """Timestamp older than 5 minutes → 401."""
    secret = b"secret-stale-ts-test"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    body = json.dumps(_make_callback_body()).encode()
    stale_ts = (datetime.now(tz=UTC) - timedelta(seconds=400)).isoformat()
    _, sig = _sign_body(secret, body, ts=stale_ts)

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=secret),
        ),
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": stale_ts,
                "X-RAC-Signature-256": sig,
            },
        )

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scenario 7: replay (correct HMAC, stale timestamp) → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_stale_body_rejected(client: AsyncClient, db_setup: Any) -> None:
    """A valid body + valid HMAC but with a stale timestamp is rejected as a replay."""
    secret = b"secret-replay-test"
    sub = await _create_submission(db_setup)
    sub_id = sub.id

    body = json.dumps(_make_callback_body()).encode()
    # Old timestamp (> max_age_seconds = 300 s)
    old_ts = (datetime.now(tz=UTC) - timedelta(seconds=600)).isoformat()
    # Signature is valid for the old timestamp
    old_sig = compute_signature(secret, old_ts, body)

    with (
        patch(
            "rac_control_plane.api.routes.webhooks._fetch_callback_secret",
            new=AsyncMock(return_value=secret),
        ),
    ):
        resp = await client.post(
            f"/webhooks/pipeline-callback/{sub_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-RAC-Timestamp": old_ts,
                "X-RAC-Signature-256": old_sig,
            },
        )

    assert resp.status_code == 401
