# pattern: Functional Core

import base64
import datetime as dt
import hmac
import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from rac_shim.token.claims import RacTokenClaims


class InvalidCookie(Exception):  # noqa: N818
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def _compute_mac(payload_b64: str, hmac_secret: bytes) -> str:
    mac = hmac.new(hmac_secret, payload_b64.encode(), sha256).digest()
    return _b64encode(mac)


def _slug_from_aud(aud: str) -> str:
    """Extract slug from 'rac-app:{slug}'."""
    prefix = "rac-app:"
    if aud.startswith(prefix):
        return aud[len(prefix) :]
    return aud


def build_cookie_value(
    claims: RacTokenClaims,
    *,
    hmac_secret: bytes,
    issued_at: datetime,
) -> str:
    """Returns cookie value: base64url(payload).base64url(mac).

    Payload JSON fields: jti, exp (iso8601), app_slug, iat (iso8601).
    """
    payload = {
        "jti": str(claims.jti),
        "exp": claims.exp.isoformat(),
        "app_slug": _slug_from_aud(claims.aud),
        "iat": issued_at.isoformat(),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = _b64encode(payload_json.encode())
    mac_b64 = _compute_mac(payload_b64, hmac_secret)
    return f"{payload_b64}.{mac_b64}"


def build_cookie_header(
    claims: RacTokenClaims,
    *,
    hmac_secret: bytes,
    issued_at: datetime,
    max_age_seconds: int,
    cookie_domain: str,
) -> str:
    """Returns full Set-Cookie header value.

    Format: 'rac_session=<value>; Path=/; Domain=...; Max-Age=...;
             HttpOnly; Secure; SameSite=Lax'
    """
    value = build_cookie_value(claims, hmac_secret=hmac_secret, issued_at=issued_at)
    return (
        f"rac_session={value}; "
        f"Path=/; "
        f"Domain={cookie_domain}; "
        f"Max-Age={max_age_seconds}; "
        f"HttpOnly; "
        f"Secure; "
        f"SameSite=Lax"
    )


def extract_session_jti(
    cookie_header_value: str | None,
    *,
    hmac_secret: bytes,
    now: datetime,
) -> UUID | None:
    """Parse a rac_session cookie value, verify the MAC, check exp > now.

    Returns None on missing/invalid/expired cookie (never raises).
    The rac_session cookie value may be the raw value (not the full Set-Cookie
    header); this function handles both 'rac_session=<val>' and plain '<val>'.
    """
    if not cookie_header_value:
        return None

    # Strip 'rac_session=' prefix if present (e.g. from Cookie: header)
    value = cookie_header_value
    if value.startswith("rac_session="):
        value = value[len("rac_session=") :]

    # Find just the cookie value (before any ';')
    value = value.split(";")[0].strip()

    parts = value.split(".")
    if len(parts) != 2:  # noqa: PLR2004
        return None

    payload_b64, received_mac_b64 = parts

    # Constant-time MAC verification
    expected_mac = _compute_mac(payload_b64, hmac_secret)
    if not hmac.compare_digest(expected_mac, received_mac_b64):
        return None

    try:
        payload_bytes = _b64decode(payload_b64)
        payload = json.loads(payload_bytes)
    except Exception:
        return None

    # Check expiry
    try:
        exp_str = payload["exp"]
        exp_dt = datetime.fromisoformat(exp_str)
        # Ensure timezone-aware comparison
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=dt.UTC)
        now_aware = now if now.tzinfo is not None else now.replace(tzinfo=dt.UTC)
        if exp_dt <= now_aware:
            return None
    except Exception:
        return None

    try:
        return UUID(payload["jti"])
    except Exception:
        return None
