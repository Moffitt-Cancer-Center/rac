# pattern: Imperative Shell
"""Application settings loaded from environment variables with RAC_SHIM_ prefix."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ShimSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAC_SHIM_")

    # Database
    database_dsn: str  # asyncpg DSN e.g. postgresql://user:pass@host:5432/db

    # Key Vault
    kv_uri: str  # e.g. https://kv-name.vault.azure.net/

    # Routing
    parent_domain: str  # e.g. rac.example.org
    aca_internal_suffix: str  # e.g. internal.purplefield-12345.eastus.azurecontainerapps.io

    # JWT validation
    issuer: str  # expected JWT iss claim

    # Cookie
    cookie_hmac_secret: SecretStr
    cookie_domain: str  # e.g. .rac.example.org (leading dot for subdomain sharing)
    cookie_max_age_seconds: int = 86400  # 24h

    # Cold start
    wake_budget_seconds: int = 20
    cold_start_threshold_ms: int = 3000

    # Branding
    institution_name: str
    brand_logo_url: str | None = None
    researcher_contact_email_template: str = "{pi_oid}@{institution_domain}"

    # Batch writer
    batch_writer_batch_size: int = 5000
    batch_writer_flush_interval_seconds: float = 2.0
    batch_writer_max_queue_size: int = 50000

    # App registry
    app_registry_refresh_interval_seconds: int = 30

    # Environment
    env: Literal["dev", "staging", "prod"]

    # Observability
    metrics_enabled: bool = False
    otlp_endpoint: str = "http://localhost:4317"


@lru_cache(maxsize=1)
def get_settings() -> ShimSettings:
    """Return the singleton ShimSettings instance (cached)."""
    return ShimSettings()  # type: ignore[call-arg]
