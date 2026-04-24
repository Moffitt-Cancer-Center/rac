# pattern: Functional Core
"""Pure template rendering using string.Template (no Jinja).

Verifies: rac-v1.AC7.2, AC7.3, AC7.4, AC12.2, AC12.3
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Literal

TEMPLATES_DIR = Path(__file__).parent / "templates"

ErrorCode = Literal["expired", "revoked", "generic", "no_token"]


@dataclass(frozen=True)
class ErrorContext:
    institution_name: str
    brand_logo_url: str | None
    researcher_contact_email: str | None  # None for revoked + generic
    pi_name: str | None  # None for revoked + generic
    correlation_id: str


@dataclass(frozen=True)
class InterstitialContext:
    institution_name: str
    brand_logo_url: str | None
    access_mode: Literal["token_required", "public"]
    correlation_id: str
    wake_path: str = "/_rac/wake"


@lru_cache(maxsize=None)
def _load_template(name: str) -> str:
    """Load a template from disk. Result is cached indefinitely (server lifetime)."""
    return (TEMPLATES_DIR / f"{name}.html").read_text(encoding="utf-8")


def _brand_logo_html(brand_logo_url: str | None) -> str:
    """Return <img> tag or empty string based on whether a logo URL is set."""
    if brand_logo_url:
        return f'<img src="{html.escape(brand_logo_url)}" alt="logo">'
    return ""


def _safe_escape(value: str | None) -> str:
    """HTML-escape a value; treat None as empty string."""
    if value is None:
        return ""
    return html.escape(str(value))


def render_error(code: ErrorCode, context: ErrorContext) -> bytes:
    """Return rendered HTML bytes for an error page.

    Template selection (AC7.4):
    - expired → error_expired.html
    - revoked → error_revoked.html
    - generic | no_token | any unknown → error_generic.html

    All substitutions are HTML-escaped to prevent XSS.
    """
    if code == "expired":
        template_name = "error_expired"
    elif code == "revoked":
        template_name = "error_revoked"
    else:
        # generic, no_token, and all other codes → same generic page (AC7.4)
        template_name = "error_generic"

    raw = _load_template(template_name)
    substitutions = {
        "institution_name": _safe_escape(context.institution_name),
        "brand_logo_html": _brand_logo_html(context.brand_logo_url),
        "researcher_contact_email": _safe_escape(context.researcher_contact_email),
        "pi_name": _safe_escape(context.pi_name),
        "correlation_id": _safe_escape(context.correlation_id),
    }
    rendered = Template(raw).safe_substitute(substitutions)
    return rendered.encode("utf-8")


def render_interstitial(context: InterstitialContext) -> bytes:
    """Return rendered interstitial HTML bytes (AC6.2).

    If access_mode='public', inserts a Public access mode banner.
    """
    raw = _load_template("interstitial")
    public_banner = (
        '<div class="public-banner">Public access mode</div>'
        if context.access_mode == "public"
        else ""
    )
    substitutions = {
        "institution_name": _safe_escape(context.institution_name),
        "brand_logo_html": _brand_logo_html(context.brand_logo_url),
        "public_banner_html": public_banner,
        "correlation_id": _safe_escape(context.correlation_id),
        "wake_path": _safe_escape(context.wake_path),
    }
    rendered = Template(raw).safe_substitute(substitutions)
    return rendered.encode("utf-8")
