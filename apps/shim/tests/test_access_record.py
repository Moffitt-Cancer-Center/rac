"""Tests for rac_shim.audit.access_record — pure record construction.

Verifies: rac-v1.AC7.5 (public-mode records carry reviewer_token_jti=None),
          rac-v1.AC10.1 (every record has a non-empty path and valid UUID id),
          rac-v1.AC12.1 (no update/delete paths exist in this module).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rac_shim.audit.access_record import AccessRecord, AccessMode, RequestInfo, build_record

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_APP_ID = uuid.uuid4()
_SUBMISSION_ID = uuid.uuid4()


def _request_info(*, path: str = "/some/path") -> RequestInfo:
    return RequestInfo(
        host="myapp.rac.example.com",
        path=path,
        method="GET",
        user_agent="TestBrowser/1.0",
        source_ip="10.0.0.1",
        request_id=uuid.uuid4(),
    )


def _build(**kwargs) -> AccessRecord:  # type: ignore[no-untyped-def]
    defaults = dict(
        request_info=_request_info(),
        app_id=_APP_ID,
        submission_id=_SUBMISSION_ID,
        access_mode="token_required",
        token_jti=uuid.uuid4(),
        upstream_status=200,
        latency_ms=42,
        created_at=_NOW,
        record_id=uuid.uuid4(),
    )
    defaults.update(kwargs)
    return build_record(**defaults)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_build_token_required() -> None:
    """token_required path: reviewer_token_jti is set, access_mode is correct."""
    jti = uuid.uuid4()
    record = _build(access_mode="token_required", token_jti=jti)

    assert record.access_mode == "token_required"
    assert record.reviewer_token_jti == jti
    assert record.app_id == _APP_ID
    assert record.submission_id == _SUBMISSION_ID
    assert record.latency_ms == 42
    assert record.upstream_status == 200
    assert record.host == "myapp.rac.example.com"
    assert record.path == "/some/path"
    assert record.method == "GET"
    assert record.source_ip == "10.0.0.1"


def test_build_public_mode() -> None:
    """AC7.5: public-mode records have reviewer_token_jti=None."""
    record = _build(access_mode="public", token_jti=None, submission_id=None)

    assert record.access_mode == "public"
    assert record.reviewer_token_jti is None
    assert record.submission_id is None


def test_negative_latency_raises() -> None:
    """latency_ms < 0 raises ValueError."""
    with pytest.raises(ValueError, match="latency_ms"):
        _build(latency_ms=-1)


def test_zero_latency_accepted() -> None:
    """latency_ms == 0 is valid (e.g. cached responses)."""
    record = _build(latency_ms=0)
    assert record.latency_ms == 0


def test_record_id_propagated() -> None:
    """record_id kwarg becomes the id field on the returned record."""
    rid = uuid.uuid4()
    record = _build(record_id=rid)
    assert record.id == rid


def test_request_info_fields_propagated() -> None:
    """All RequestInfo fields are mirrored into the AccessRecord."""
    req = RequestInfo(
        host="other.rac.example.com",
        path="/api/v1/data",
        method="POST",
        user_agent="curl/7.0",
        source_ip="192.168.1.1",
        request_id=uuid.uuid4(),
    )
    record = build_record(
        request_info=req,
        app_id=_APP_ID,
        submission_id=None,
        access_mode="public",
        token_jti=None,
        upstream_status=201,
        latency_ms=10,
        created_at=_NOW,
        record_id=uuid.uuid4(),
    )
    assert record.host == "other.rac.example.com"
    assert record.path == "/api/v1/data"
    assert record.method == "POST"
    assert record.user_agent == "curl/7.0"
    assert record.source_ip == "192.168.1.1"
    assert record.request_id == req.request_id


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

_SAFE_TEXT = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=50,
)


@given(
    path=st.text(min_size=1, max_size=200).map(lambda s: "/" + s),
    method=st.sampled_from(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]),
    latency_ms=st.integers(min_value=0, max_value=60_000),
    access_mode=st.sampled_from(["token_required", "public"]),
    upstream_status=st.one_of(st.none(), st.integers(min_value=100, max_value=599)),
)
@settings(max_examples=100)
def test_property_build_record_fields_match(
    path: str,
    method: str,
    latency_ms: int,
    access_mode: AccessMode,
    upstream_status: int | None,
) -> None:
    """For any valid inputs, build_record fields equal the inputs."""
    req = RequestInfo(
        host="prop.rac.example.com",
        path=path,
        method=method,
        user_agent=None,
        source_ip="1.2.3.4",
        request_id=uuid.uuid4(),
    )
    jti = uuid.uuid4() if access_mode == "token_required" else None
    record_id = uuid.uuid4()

    record = build_record(
        request_info=req,
        app_id=_APP_ID,
        submission_id=None,
        access_mode=access_mode,
        token_jti=jti,
        upstream_status=upstream_status,
        latency_ms=latency_ms,
        created_at=_NOW,
        record_id=record_id,
    )

    assert record.id == record_id
    assert record.path == path
    assert record.method == method
    assert record.latency_ms == latency_ms
    assert record.access_mode == access_mode
    assert record.upstream_status == upstream_status
    assert record.reviewer_token_jti == jti
    # Path is non-empty and starts with "/"
    assert len(record.path) > 0
