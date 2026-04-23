"""Submission request/response schemas.

Type-only module for Pydantic models (no FCIS tag needed).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, HttpUrl

from rac_control_plane.data.models import SubmissionStatus


class SubmissionCreateRequest(BaseModel):
    """Request body for creating a submission."""

    github_repo_url: HttpUrl
    git_ref: str = "main"
    dockerfile_path: str = "Dockerfile"
    paper_title: str | None = None
    pi_principal_id: UUID
    dept_fallback: str
    manifest: dict[str, Any] | None = None


class SubmissionResponse(BaseModel):
    """Response model for a submission."""

    id: UUID
    slug: str
    status: SubmissionStatus
    submitter_principal_id: UUID
    agent_id: UUID | None = None
    github_repo_url: str
    git_ref: str
    dockerfile_path: str
    pi_principal_id: UUID
    dept_fallback: str
    manifest: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class SubmissionListResponse(BaseModel):
    """Paginated list response for submissions."""

    items: list[SubmissionResponse]
    total: int
    page: int
    page_size: int
