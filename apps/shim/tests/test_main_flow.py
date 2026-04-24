"""Integration tests for rac_shim.main (AC7.* and AC6.2).

Strategy:
- Create _Deps with stub objects for all external services
- Inject a stub KeyVaultPublicKeyCache that holds a pre-generated ECKey
- Inject a stub AppRegistry populated directly (no DB polling)
- Inject a respx MockRouter for upstream http calls
- Inject a stub DenylistCache for controlled revocation tests
- Inject a stub BatchWriter that stores records in memory
"""
from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from joserfc import jwt
from joserfc.jwk import ECKey
from starlette.testclient import TestClient

from rac_shim.main import _Deps, create_app
from rac_shim.routing.decision import AppRoute

PARENT_DOMAIN = "rac.example.org"
ISSUER = "https://control-plane.rac.example.org"
SLUG = "foo"
UPSTREAM_HOST = "upstream.internal"

# ---------------------------------------------------------------------------
# Module-level key pair shared across all tests (expensive to generate)
# ---------------------------------------------------------------------------

_PRIV_KEY: ECKey
_PUB_KEY: ECKey


def _ensure_keys() -> tuple[ECKey, ECKey]:
    global _PRIV_KEY, _PUB_KEY
    try:
        return _PRIV_KEY, _PUB_KEY
    except NameError:
        priv_raw = generate_private_key(SECP256R1(), default_backend())
        _PRIV_KEY = ECKey.import_key(priv_raw)
        _PUB_KEY = ECKey.import_key(_PRIV_KEY.as_dict(private=False))
        return _PRIV_KEY, _PUB_KEY


# ---------------------------------------------------------------------------
# JWT minting helper
# ---------------------------------------------------------------------------


def _mint_token(
    private_key: ECKey,
    *,
    slug: str = SLUG,
    issuer: str = ISSUER,
    exp_delta: float = 3600,
    jti: uuid.UUID | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    jti_val = str(jti or uuid.uuid4())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": f"rac-app:{slug}",
        "sub": "reviewer-test",
        "jti": jti_val,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=exp_delta)).timestamp()),
    }
    header = {"alg": "ES256", "kid": "test-kid"}
    return jwt.encode(header, claims, private_key)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Stub dependencies
# ---------------------------------------------------------------------------


class _StubSettings:
    parent_domain = PARENT_DOMAIN
    issuer = ISSUER
    cookie_domain = f".{PARENT_DOMAIN}"
    cookie_max_age_seconds = 3600
    institution_name = "Test University"
    brand_logo_url = None
    cold_start_threshold_ms = 3000
    wake_budget_seconds = 20

    def __init__(self, hmac_secret: bytes = b"test-secret-12345678901234567890") -> None:
        self._hmac = hmac_secret.decode(errors="replace")

    @property
    def cookie_hmac_secret(self) -> Any:  # type: ignore[return]
        class _Wrapper:
            def __init__(self, val: str) -> None:
                self._val = val

            def get_secret_value(self) -> str:
                return self._val

        return _Wrapper(self._hmac)


class _StubKvCache:
    def __init__(self, public_key: ECKey) -> None:
        self._key = public_key

    async def get_jwk(self, key_name: str) -> ECKey:
        return self._key


class _StubRegistry:
    def __init__(self, routes: dict[str, AppRoute]) -> None:
        self._routes = routes

    def get(self, slug: str) -> AppRoute | None:
        return self._routes.get(slug)

    def all(self) -> dict[str, AppRoute]:
        return dict(self._routes)


class _StubDenylist:
    def __init__(self, revoked: set[uuid.UUID] | None = None) -> None:
        self._revoked: set[uuid.UUID] = revoked or set()

    async def check(self, jti: uuid.UUID) -> bool:
        return jti in self._revoked

    def revoke(self, jti: uuid.UUID) -> None:
        self._revoked.add(jti)


class _StubWriter:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def append(self, record: Any) -> None:
        self.records.append(record)


# ---------------------------------------------------------------------------
# Test client factory
# ---------------------------------------------------------------------------


def _make_test_client(
    *,
    routes: dict[str, AppRoute] | None = None,
    public_key: ECKey | None = None,
    denylist: _StubDenylist | None = None,
    upstream_router: respx.MockRouter | None = None,
    writer: _StubWriter | None = None,
    settings: _StubSettings | None = None,
) -> tuple[TestClient, _StubWriter, _StubDenylist]:
    priv, pub = _ensure_keys()
    if settings is None:
        settings = _StubSettings()
    if denylist is None:
        denylist = _StubDenylist()
    if writer is None:
        writer = _StubWriter()
    if public_key is None:
        public_key = pub

    if routes is None:
        routes = {
            SLUG: AppRoute(
                slug=SLUG,
                app_id=uuid.uuid4(),
                upstream_host=UPSTREAM_HOST,
                access_mode="token_required",
            )
        }

    transport = (
        httpx.MockTransport(upstream_router.handler) if upstream_router else None
    )
    http_client = httpx.AsyncClient(transport=transport)

    deps = _Deps()
    deps.kv_key_cache = _StubKvCache(public_key)  # type: ignore[assignment]
    deps.denylist_cache = denylist  # type: ignore[assignment]
    deps.batch_writer = writer  # type: ignore[assignment]
    deps.app_registry = _StubRegistry(routes)  # type: ignore[assignment]
    deps.httpx_client = http_client
    deps.settings = settings

    app = create_app(deps=deps)
    client = TestClient(
        app,
        base_url=f"https://{SLUG}.{PARENT_DOMAIN}",
        raise_server_exceptions=False,
    )
    return client, writer, denylist


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_token_sets_cookie_and_redirects() -> None:
    """Valid token in query param → 302 redirect with Set-Cookie (AC7.1)."""
    priv, pub = _ensure_keys()
    token_str = _mint_token(priv)

    router = respx.MockRouter()
    client, writer, _ = _make_test_client(public_key=pub, upstream_router=router)

    resp = client.get(
        f"/?rac_token={token_str}",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}: {resp.text}"
    assert "Set-Cookie" in resp.headers
    cookie_header = resp.headers["Set-Cookie"]
    assert "rac_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=Lax" in cookie_header
    assert len(writer.records) >= 1


def test_expired_token_shows_expired_page() -> None:
    """Expired token → 403 with error_expired HTML (AC7.3)."""
    priv, pub = _ensure_keys()
    token_str = _mint_token(priv, exp_delta=-10)  # already expired

    client, _, _ = _make_test_client(public_key=pub)
    resp = client.get(
        f"/?rac_token={token_str}",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    assert "expired" in resp.text.lower()


def test_revoked_token_shows_revoked_page() -> None:
    """Revoked jti → 403 error_revoked (AC7.2)."""
    priv, pub = _ensure_keys()
    jti = uuid.uuid4()
    token_str = _mint_token(priv, jti=jti)
    denylist = _StubDenylist(revoked={jti})

    client, _, _ = _make_test_client(public_key=pub, denylist=denylist)
    resp = client.get(
        f"/?rac_token={token_str}",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    assert "revoked" in resp.text.lower()


def test_malformed_token_generic_page() -> None:
    """Malformed token → 403 error_generic with no detail leakage (AC7.4)."""
    client, _, _ = _make_test_client()
    resp = client.get(
        "/?rac_token=not.a.valid.jwt",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 403
    body_lower = resp.text.lower()
    for forbidden in ("signature", "audience", "issuer", "malformed", "traceback"):
        assert forbidden not in body_lower, f"Forbidden word in body: {forbidden!r}"


def test_wrong_audience_generic_page() -> None:
    """Token for 'bar' presented at 'foo' → 403 error_generic (AC7.6)."""
    priv, pub = _ensure_keys()
    token_str = _mint_token(priv, slug="bar")  # audience = rac-app:bar

    client, _, _ = _make_test_client(public_key=pub)
    resp = client.get(
        f"/?rac_token={token_str}",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 403
    body_lower = resp.text.lower()
    assert "audience" not in body_lower


def test_public_mode_no_token_needed() -> None:
    """Public app serves requests without any token (AC7.5)."""
    routes = {
        SLUG: AppRoute(
            slug=SLUG,
            app_id=uuid.uuid4(),
            upstream_host=UPSTREAM_HOST,
            access_mode="public",
        )
    }
    router = respx.MockRouter()
    router.get(f"http://{UPSTREAM_HOST}/").mock(
        return_value=httpx.Response(200, content=b"app content")
    )

    client, writer, _ = _make_test_client(routes=routes, upstream_router=router)
    resp = client.get(
        "/",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert len(writer.records) >= 1
    assert writer.records[-1].reviewer_token_jti is None
    assert writer.records[-1].access_mode == "public"


def test_cold_start_serves_interstitial() -> None:
    """Upstream returns timeout → shim serves interstitial HTML (AC6.2)."""
    priv, pub = _ensure_keys()
    token_str = _mint_token(priv)

    # First: get cookie via token redirect
    router1 = respx.MockRouter()
    client1, _, _ = _make_test_client(public_key=pub, upstream_router=router1)
    resp1 = client1.get(
        f"/?rac_token={token_str}",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
        follow_redirects=False,
    )
    assert resp1.status_code == 302
    cookie_header = resp1.headers.get("Set-Cookie", "")
    rac_cookie = ""
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("rac_session="):
            rac_cookie = part
            break

    # Second: upstream times out → interstitial
    router2 = respx.MockRouter()
    router2.get(f"http://{UPSTREAM_HOST}/").mock(
        side_effect=httpx.TimeoutException("cold start")
    )
    # Re-use same deps but with new transport
    client2, _, _ = _make_test_client(public_key=pub, upstream_router=router2)
    resp2 = client2.get(
        "/",
        headers={
            "Host": f"{SLUG}.{PARENT_DOMAIN}",
            "Cookie": rac_cookie,
        },
    )
    assert resp2.status_code == 200, f"Expected 200 interstitial, got {resp2.status_code}: {resp2.text}"
    assert "waking up" in resp2.text.lower()


def test_correlation_id_in_error_response() -> None:
    """Error response body contains a Reference ID (AC12.2)."""
    client, _, _ = _make_test_client()
    resp = client.get(
        "/?rac_token=not.valid.jwt",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 403
    assert "Reference ID" in resp.text
    corr_id = resp.headers.get("X-Correlation-Id", "")
    if corr_id:
        assert corr_id in resp.text


def test_unknown_host_returns_404() -> None:
    """Request for an unknown host returns 404."""
    client, _, _ = _make_test_client()
    resp = client.get(
        "/",
        headers={"Host": "unknown.rac.example.org"},
    )
    assert resp.status_code == 404


def test_health_endpoint() -> None:
    """/_shim/health returns 200."""
    client, _, _ = _make_test_client()
    resp = client.get("/_shim/health")
    assert resp.status_code == 200


def test_no_token_no_cookie_returns_403() -> None:
    """No rac_token and no rac_session cookie → 403 generic error page."""
    client, _, _ = _make_test_client()
    resp = client.get(
        "/",
        headers={"Host": f"{SLUG}.{PARENT_DOMAIN}"},
    )
    assert resp.status_code == 403
    # Generic page — no validation detail
    body_lower = resp.text.lower()
    assert "access denied" in body_lower or "access" in body_lower
