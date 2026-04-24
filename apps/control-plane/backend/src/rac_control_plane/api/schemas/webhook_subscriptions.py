# pattern: Functional Core
"""Pydantic schemas for webhook subscription CRUD endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class WebhookSubscriptionCreate(BaseModel):
    """Request body for creating a webhook subscription."""

    name: str
    callback_url: str
    event_types: list[str]


class WebhookSubscriptionUpdate(BaseModel):
    """Request body for updating a webhook subscription (all fields optional)."""

    name: str | None = None
    callback_url: str | None = None
    event_types: list[str] | None = None
    enabled: bool | None = None
    reset_failures: bool = False


class WebhookSubscriptionResponse(BaseModel):
    """Webhook subscription detail response (no secret)."""

    id: UUID
    name: str
    callback_url: str
    event_types: list[str]
    enabled: bool
    consecutive_failures: int
    last_delivery_at: datetime | None
    secret_rotated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WebhookSubscriptionCreateResponse(WebhookSubscriptionResponse):
    """Create response — includes the plaintext secret ONCE."""

    secret: str
