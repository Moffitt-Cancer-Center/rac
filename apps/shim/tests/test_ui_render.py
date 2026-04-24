"""Tests for rac_shim.ui.render (AC7.2-AC7.4, AC12.2, AC12.3)."""
from __future__ import annotations

import pytest

from rac_shim.ui.render import (
    ErrorContext,
    InterstitialContext,
    render_error,
    render_interstitial,
)

CORR_ID = "test-corr-id-12345"
INSTITUTION = "Moffitt Cancer Center"


def _make_error_ctx(**overrides: object) -> ErrorContext:
    defaults: dict[str, object] = {
        "institution_name": INSTITUTION,
        "brand_logo_url": None,
        "researcher_contact_email": "pi@example.org",
        "pi_name": "Dr. Doe",
        "correlation_id": CORR_ID,
    }
    defaults.update(overrides)
    return ErrorContext(**defaults)  # type: ignore[arg-type]


def _make_interstitial_ctx(**overrides: object) -> InterstitialContext:
    defaults: dict[str, object] = {
        "institution_name": INSTITUTION,
        "brand_logo_url": None,
        "access_mode": "token_required",
        "correlation_id": CORR_ID,
    }
    defaults.update(overrides)
    return InterstitialContext(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Error page tests
# ---------------------------------------------------------------------------


def test_render_expired_includes_contact() -> None:
    """error_expired page shows PI name and contact email (AC7.3)."""
    html = render_error("expired", _make_error_ctx(pi_name="Dr. Doe", researcher_contact_email="doe@example.org")).decode()
    assert "Dr. Doe" in html
    assert "doe@example.org" in html


def test_render_revoked_has_no_pi() -> None:
    """error_revoked page does NOT include the PI name (revoked is generic, AC7.2)."""
    html = render_error("revoked", _make_error_ctx(pi_name="Dr. Doe")).decode()
    assert "Dr. Doe" not in html


def test_render_generic_leaks_no_validation_detail() -> None:
    """error_generic page contains no validation-specific detail (AC7.4)."""
    html_lower = render_error("generic", _make_error_ctx()).decode().lower()
    for forbidden in ("signature", "audience", "issuer", "malformed", "traceback"):
        assert forbidden not in html_lower, f"Forbidden word found: {forbidden!r}"


def test_render_interstitial_public_banner() -> None:
    """access_mode='public' shows the public-access banner."""
    html = render_interstitial(_make_interstitial_ctx(access_mode="public")).decode()
    assert "Public access mode" in html


def test_render_interstitial_token_required_no_banner() -> None:
    """access_mode='token_required' does NOT show the public banner."""
    html = render_interstitial(_make_interstitial_ctx(access_mode="token_required")).decode()
    assert "Public access mode" not in html


def test_correlation_id_present_in_every_page() -> None:
    """correlation_id appears in every rendered page (AC12.2)."""
    pages = [
        render_error("expired", _make_error_ctx()).decode(),
        render_error("revoked", _make_error_ctx()).decode(),
        render_error("generic", _make_error_ctx()).decode(),
        render_error("no_token", _make_error_ctx()).decode(),
        render_interstitial(_make_interstitial_ctx()).decode(),
    ]
    for page in pages:
        assert CORR_ID in page, f"Correlation ID not found in page"


def test_html_escaping() -> None:
    """pi_name containing HTML tags is escaped in rendered output (AC12.3)."""
    html = render_error(
        "expired",
        _make_error_ctx(pi_name="<script>alert(1)</script>", researcher_contact_email="safe@example.org"),
    ).decode()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_no_token_uses_generic_template() -> None:
    """no_token error code renders the generic template (AC7.4)."""
    html = render_error("no_token", _make_error_ctx()).decode()
    # Generic template says "Access denied"
    assert "Access denied" in html


def test_brand_logo_img_when_url_set() -> None:
    """brand_logo_url set → <img> tag appears in the output."""
    html = render_error(
        "generic",
        _make_error_ctx(brand_logo_url="https://example.org/logo.png"),
    ).decode()
    assert '<img src="https://example.org/logo.png"' in html


def test_brand_logo_absent_when_no_url() -> None:
    """brand_logo_url=None → no <img> tag for logo."""
    html = render_error("generic", _make_error_ctx(brand_logo_url=None)).decode()
    assert "<img" not in html


def test_interstitial_wake_path_embedded() -> None:
    """The wake_path is embedded in the interstitial JS."""
    html = render_interstitial(_make_interstitial_ctx()).decode()
    assert "/_rac/wake" in html
