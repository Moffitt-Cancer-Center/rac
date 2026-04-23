"""Agent request/response schemas.

Type-only module for Pydantic models (no FCIS tag needed).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AgentCreateRequest(BaseModel):
    """Request body for creating an agent."""

    name: str
    kind: str  # 'ui', 'servicenow', 'cli', 'other'
    entra_app_id: UUID
    metadata: dict[str, Any] | None = None
    enabled: bool = True


class AgentUpdateRequest(BaseModel):
    """Request body for updating an agent."""

    name: str | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    """Response model for an agent."""

    id: UUID
    name: str
    kind: str
    entra_app_id: str
    service_principal_id: UUID
    metadata: dict[str, Any] | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
