# pattern: Imperative Shell
import functools
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """RAC Control Plane configuration from environment variables with RAC_ prefix."""

    # Deployment settings
    env: Literal["dev", "staging", "prod"]
    institution_name: str
    parent_domain: str
    brand_logo_url: str

    # IdP (Entra) settings
    idp_tenant_id: str
    idp_client_id: str
    idp_api_client_id: str

    # Database settings
    pg_host: str
    pg_port: int = 5432
    pg_db: str
    pg_user: str
    pg_password: SecretStr
    pg_ssl_mode: str = "require"

    # Azure settings
    kv_uri: str
    blob_account_url: str
    acr_login_server: str
    aca_env_resource_id: str

    # Scan settings
    scan_severity_gate: Literal["critical", "high", "medium", "low"]

    # Approver roles
    approver_role_research: str
    approver_role_it: str

    # Webhook settings
    webhook_secret_rotation_days: int = 30
    webhook_secret_grace_period_hours: int = 24
    webhook_max_consecutive_failures: int = 10
    internal_job_secret: SecretStr | None = None

    # GitHub pipeline dispatch settings
    gh_pipeline_owner: str = ""
    gh_pipeline_repo: str = "rac-pipeline"
    gh_app_id: str | None = None
    gh_app_private_key: SecretStr | None = None
    gh_pat: SecretStr | None = None  # fallback for dev; prefer App auth in prod
    pipeline_timeout_minutes: int = 120

    # Callback URL — the Control Plane's own base URL that the pipeline POSTs back to
    callback_base_url: str = ""

    # Observability
    otlp_endpoint: str = "http://localhost:4317"
    metrics_enabled: bool = False

    model_config = {"env_prefix": "RAC_"}

    @property
    def pg_dsn(self) -> str:
        """Construct async SQLAlchemy DSN."""
        password = self.pg_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.pg_user}:{password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
            f"?ssl={self.pg_ssl_mode}"
        )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance. Clear cache with get_settings.cache_clear()."""
    return Settings()  # type: ignore
