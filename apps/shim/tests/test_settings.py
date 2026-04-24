"""Tests for rac_shim.settings."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rac_shim.settings import ShimSettings


def _make_settings(**overrides: object) -> ShimSettings:
    """Build a ShimSettings from explicit kwargs (no env vars needed)."""
    base = {
        "database_dsn": "postgresql://user:pass@localhost:5432/db",
        "kv_uri": "https://kv.vault.azure.net/",
        "parent_domain": "rac.example.org",
        "aca_internal_suffix": "internal.env1.eastus.azurecontainerapps.io",
        "issuer": "https://control-plane.rac.example.org",
        "cookie_hmac_secret": "supersecrethmackey",
        "cookie_domain": ".rac.example.org",
        "institution_name": "Test University",
        "env": "dev",
    }
    base.update(overrides)
    return ShimSettings(**base)  # type: ignore[arg-type]


def test_defaults_are_sensible() -> None:
    """Settings with required fields use correct defaults."""
    s = _make_settings()
    assert s.cookie_max_age_seconds == 86400
    assert s.wake_budget_seconds == 20
    assert s.cold_start_threshold_ms == 3000
    assert s.metrics_enabled is False
    assert s.otlp_endpoint == "http://localhost:4317"


def test_brand_logo_url_optional() -> None:
    """brand_logo_url defaults to None."""
    s = _make_settings()
    assert s.brand_logo_url is None


def test_brand_logo_url_can_be_set() -> None:
    s = _make_settings(brand_logo_url="https://example.org/logo.png")
    assert s.brand_logo_url == "https://example.org/logo.png"


def test_cookie_hmac_secret_is_secret_str() -> None:
    """cookie_hmac_secret is a SecretStr and its value is accessible."""
    s = _make_settings(cookie_hmac_secret="my-secret")
    assert s.cookie_hmac_secret.get_secret_value() == "my-secret"


def test_env_literal_validation() -> None:
    """env must be one of dev/staging/prod."""
    with pytest.raises(ValidationError):
        _make_settings(env="production")  # type: ignore[arg-type]


def test_env_accepts_valid_values() -> None:
    for v in ("dev", "staging", "prod"):
        s = _make_settings(env=v)  # type: ignore[arg-type]
        assert s.env == v


def test_batch_writer_defaults() -> None:
    s = _make_settings()
    assert s.batch_writer_batch_size == 5000
    assert s.batch_writer_flush_interval_seconds == 2.0
    assert s.batch_writer_max_queue_size == 50000
