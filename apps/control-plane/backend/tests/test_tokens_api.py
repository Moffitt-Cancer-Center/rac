"""Integration tests for token management API.

POST   /apps/{app_id}/tokens
GET    /apps/{app_id}/tokens
DELETE /apps/{app_id}/tokens/{jti}

Uses a local ES256 keypair to sign tokens without real Key Vault.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import App, ReviewerToken, RevokedToken, Submission, SubmissionStatus
from rac_control_plane.services.tokens.key_probe import SignatureFormat, _reset_for_tests


# ---------------------------------------------------------------------------
# Module-level EC keypair (one per test session)
# ---------------------------------------------------------------------------

_EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_EC_PUBLIC_KEY = _EC_PRIVATE_KEY.public_key()


async def _raw_signer(digest: bytes) -> bytes:
    """Test signer: signs digest with local P-256 key, returns raw r||s."""
    der_sig = _EC_PRIVATE_KEY.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    r, s = decode_dss_signature(der_sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_token_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject test signer and settings into all token-related modules."""
    from tests.conftest_settings_helper import make_test_settings
    settings = make_test_settings()

    monkeypatch.setattr(
        "rac_control_plane.api.routes.tokens._test_signer_override",
        _raw_signer,
    )
    monkeypatch.setattr(
        "rac_control_plane.services.tokens.issuer.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "rac_control_plane.api.routes.tokens.get_settings",
        lambda: settings,
    )
    # Use RAW_R_S signature format throughout
    monkeypatch.setattr(
        "rac_control_plane.api.routes.tokens.SignatureFormat",
        SignatureFormat,
    )
    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _create_app_with_submission(
    db_setup: AsyncSession,
    *,
    owner_oid: UUID,
    slug: str | None = None,
    status: SubmissionStatus = SubmissionStatus.deployed,
) -> tuple[UUID, UUID]:
    """Insert an App + Submission pair, return (app_id, submission_id)."""
    slug = slug or f"app-{uuid4().hex[:8]}"
    app_id = uuid4()
    sub_id = uuid4()

    sub = Submission(
        id=sub_id,
        slug=slug,
        status=status,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid,
        dept_fallback="TestDept",
    )
    db_setup.add(sub)
    await db_setup.flush()

    app = App(
        id=app_id,
        slug=slug,
        pi_principal_id=owner_oid,
        dept_fallback="TestDept",
        current_submission_id=sub_id,
    )
    db_setup.add(app)
    await db_setup.flush()
    await db_setup.commit()
    return app_id, sub_id


def _decode_jws_payload(jws: str) -> dict[str, Any]:
    parts = jws.split(".")
    padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _verify_jws(jws: str, public_key: ec.EllipticCurvePublicKey) -> bool:
    parts = jws.split(".")
    if len(parts) != 3:
        return False
    signing_input = f"{parts[0]}.{parts[1]}".encode("utf-8")
    sig_b64 = parts[2]
    padded = sig_b64 + "=" * (4 - len(sig_b64) % 4)
    raw_sig = base64.urlsafe_b64decode(padded)
    r = int.from_bytes(raw_sig[:32], "big")
    s = int.from_bytes(raw_sig[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, Prehashed as _Pre
    der_sig = encode_dss_signature(r, s)
    digest = hashlib.sha256(signing_input).digest()
    try:
        public_key.verify(der_sig, digest, ec.ECDSA(_Pre(hashes.SHA256())))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# POST /apps/{app_id}/tokens — mint
# ---------------------------------------------------------------------------

async def test_submitter_mints_token_201(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """Submitter POST → 201 with jwt, jti, visit_url."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Journal Reviewer #1", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()
    assert "jwt" in data
    assert "jti" in data
    assert "visit_url" in data
    assert "expires_at" in data
    assert "rac_token=" in data["visit_url"]


async def test_jwt_verifies_against_public_key(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """Issued JWT signature verifies with the local test public key."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Verifier", "ttl_days": 7},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    jwt_str = resp.json()["jwt"]
    assert _verify_jws(jwt_str, _EC_PUBLIC_KEY)


async def test_visit_url_has_correct_form(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """visit_url starts with 'https://{slug}.test.local/?rac_token='."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(
        db_setup, owner_oid=owner_oid, slug="myslug"
    )
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "URL Reviewer", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    visit_url = resp.json()["visit_url"]
    assert visit_url.startswith("https://myslug.test.local/?rac_token=")


async def test_non_owner_non_admin_mint_returns_403(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Non-owner non-admin → 403."""
    owner_oid = uuid4()
    stranger_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=stranger_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Stranger", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_ttl_days_181_returns_422(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """ttl_days=181 → 422 (Pydantic validation)."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Test", "ttl_days": 181},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_empty_reviewer_label_returns_422(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Empty reviewer_label → 422."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_admin_can_mint(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """Admin (it_approver role) can mint tokens for any app."""
    owner_oid = uuid4()
    admin_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=admin_oid, roles=["it_approver"])

    resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Admin Reviewer", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /apps/{app_id}/tokens — list
# ---------------------------------------------------------------------------

async def test_listing_excludes_jwt(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """GET /tokens response items do not include the jwt field."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    # Mint first
    await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "List Test", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        f"/apps/{app_id}/tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert "jwt" not in item
        assert "jti" in item


async def test_listing_shows_jti(client: Any, db_setup: AsyncSession, mock_oidc: Any) -> None:
    """GET /tokens items contain the jti field."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    mint_resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "JTI Check", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    minted_jti = mint_resp.json()["jti"]

    list_resp = await client.get(
        f"/apps/{app_id}/tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    jtis = [item["jti"] for item in list_resp.json()["items"]]
    assert minted_jti in jtis


# ---------------------------------------------------------------------------
# DELETE /apps/{app_id}/tokens/{jti} — revoke
# ---------------------------------------------------------------------------

async def test_revoke_writes_revoked_token_row(
    client: Any, db_setup: AsyncSession, db_session: AsyncSession, mock_oidc: Any
) -> None:
    """DELETE /tokens/{jti} → 204 and revoked_token row inserted."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    # Mint
    mint_resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Revoke Me", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert mint_resp.status_code == 201
    jti = mint_resp.json()["jti"]

    # Revoke
    del_resp = await client.delete(
        f"/apps/{app_id}/tokens/{jti}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 204

    # Verify DB row
    stmt = select(RevokedToken).where(RevokedToken.jti == jti)
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is not None


async def test_revoke_then_list_shows_revoked_at(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """After revoke, GET with include_revoked=true shows revoked_at."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    mint_resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Rev Check", "ttl_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    jti = mint_resp.json()["jti"]

    await client.delete(
        f"/apps/{app_id}/tokens/{jti}",
        headers={"Authorization": f"Bearer {token}"},
    )

    list_resp = await client.get(
        f"/apps/{app_id}/tokens?include_revoked=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    items = list_resp.json()["items"]
    revoked_items = [i for i in items if i["jti"] == jti]
    assert len(revoked_items) == 1
    assert revoked_items[0]["revoked_at"] is not None


async def test_non_owner_revoke_returns_403(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Non-owner cannot revoke tokens."""
    owner_oid = uuid4()
    stranger_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    # Mint as owner
    owner_token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    mint_resp = await client.post(
        f"/apps/{app_id}/tokens",
        json={"reviewer_label": "Forbidden Revoke", "ttl_days": 30},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    jti = mint_resp.json()["jti"]

    # Revoke as stranger
    stranger_token = mock_oidc.issue_user_token(oid=stranger_oid, roles=[])
    del_resp = await client.delete(
        f"/apps/{app_id}/tokens/{jti}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert del_resp.status_code == 403


async def test_revoke_unknown_jti_returns_404(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """DELETE with unknown jti → 404."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)
    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    del_resp = await client.delete(
        f"/apps/{app_id}/tokens/{uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 404
