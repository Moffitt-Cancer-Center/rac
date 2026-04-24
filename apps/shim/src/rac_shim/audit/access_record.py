# pattern: Functional Core
"""Pure access_log record construction.

Verifies: rac-v1.AC10.1 (every proxied request has a record),
          rac-v1.AC7.5  (public-mode records carry token_jti=NULL),
          rac-v1.AC12.1 (append-only — no update/delete paths exist here).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

AccessMode = Literal["token_required", "public"]


@dataclass(frozen=True)
class RequestInfo:
    """Immutable view of the inbound HTTP request metadata."""

    host: str
    path: str
    method: str
    user_agent: str | None
    source_ip: str
    request_id: UUID


@dataclass(frozen=True)
class AccessRecord:
    """One row destined for the ``access_log`` table (append-only)."""

    id: UUID
    app_id: UUID
    submission_id: UUID | None
    reviewer_token_jti: UUID | None
    access_mode: AccessMode
    host: str
    path: str
    method: str
    upstream_status: int | None
    latency_ms: int
    user_agent: str | None
    source_ip: str
    created_at: datetime
    request_id: UUID


def build_record(
    *,
    request_info: RequestInfo,
    app_id: UUID,
    submission_id: UUID | None,
    access_mode: AccessMode,
    token_jti: UUID | None,
    upstream_status: int | None,
    latency_ms: int,
    created_at: datetime,
    record_id: UUID,
) -> AccessRecord:
    """Construct an AccessRecord from request context.

    Pure: no I/O, no side effects.

    Args:
        request_info:    Metadata extracted from the inbound request.
        app_id:          UUID of the target app row.
        submission_id:   Current submission UUID (may be None for public apps).
        access_mode:     'token_required' or 'public'.
        token_jti:       The reviewer token jti (None for public access or on
                         validation failure before proxy).
        upstream_status: HTTP status returned by the upstream app (None if
                         the request never reached the upstream).
        latency_ms:      Total proxy round-trip latency in milliseconds (>= 0).
        created_at:      UTC datetime stamp for the record.
        record_id:       UUID for the new row (caller injects; use UUIDv7 in prod).

    Raises:
        ValueError: if latency_ms < 0.
    """
    if latency_ms < 0:
        raise ValueError(f"latency_ms must be >= 0, got {latency_ms}")

    return AccessRecord(
        id=record_id,
        app_id=app_id,
        submission_id=submission_id,
        reviewer_token_jti=token_jti,
        access_mode=access_mode,
        host=request_info.host,
        path=request_info.path,
        method=request_info.method,
        upstream_status=upstream_status,
        latency_ms=latency_ms,
        user_agent=request_info.user_agent,
        source_ip=request_info.source_ip,
        created_at=created_at,
        request_id=request_info.request_id,
    )
