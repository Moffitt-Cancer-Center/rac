# pattern: Imperative Shell
"""Reviewer token issuer: signs with Key Vault and persists to Postgres.

Orchestrates:
  - Pure claim building (claim_builder)
  - Pure JWS assembly (jws_assembly)
  - Pure DER decode when needed (signature_decode)
  - Shell: digest + sign (Key Vault), DB insert (reviewer_token + approval_event)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import ApprovalEvent, ReviewerToken
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.tokens.claim_builder import build_reviewer_claims
from rac_control_plane.services.tokens.jws_assembly import assemble_jws, build_signing_input
from rac_control_plane.services.tokens.key_probe import SignatureFormat
from rac_control_plane.services.tokens.signature_decode import der_to_raw_r_s
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IssuedToken:
    """Result of a successful token issuance."""
    jwt: str
    jti: UUID
    expires_at: datetime
    reviewer_label: str


async def issue_reviewer_token(
    session: AsyncSession,
    *,
    app_id: UUID,
    app_slug: str,
    reviewer_label: str,
    ttl_days: int,
    actor_principal_id: UUID,
    signer: Callable[[bytes], Awaitable[bytes]] | None = None,
    signature_format: SignatureFormat | None = None,
    issuer: str | None = None,
    now: datetime | None = None,
) -> IssuedToken:
    """Issue a reviewer token, sign it, and persist the record.

    Args:
        session: Async SQLAlchemy session (caller commits).
        app_id: UUID of the app this token grants access to.
        app_slug: Slug of the app (used in aud claim and kid).
        reviewer_label: Human-readable label for the token (e.g. "Reviewer #1").
        ttl_days: Token validity in days (must be <= settings.max_reviewer_token_ttl_days).
        actor_principal_id: OID of the principal issuing this token.
        signer: Optional async callable(digest: bytes) -> signature bytes.
                Defaults to real Key Vault CryptographyClient.
        signature_format: Override format detection for tests.  Defaults to module cache.
        issuer: JWT iss claim.  Defaults to settings.issuer.
        now: Override current time for tests.

    Returns:
        IssuedToken dataclass with the compact JWS, jti, expires_at, reviewer_label.

    Raises:
        ValidationApiError: ttl_days exceeds the configured maximum.
    """
    settings = get_settings()

    # 1. Validate TTL
    max_ttl = settings.max_reviewer_token_ttl_days
    if ttl_days > max_ttl:
        raise ValidationApiError(
            code="ttl_exceeds_max",
            public_message=(
                f"ttl_days {ttl_days} exceeds the maximum allowed ({max_ttl})."
            ),
        )
    if ttl_days < 1:
        raise ValidationApiError(
            code="ttl_too_short",
            public_message="ttl_days must be at least 1.",
        )

    # 2. Generate jti (uuid4 — NOT uuidv7; avoid leaking issuance timestamp order)
    jti = uuid.uuid4()

    # 3. Compute iat and exp
    iat = now if now is not None else datetime.now(UTC)
    exp = iat + timedelta(days=ttl_days)

    # 4. Build claims
    effective_issuer = issuer if issuer is not None else (settings.issuer or "")
    claims = build_reviewer_claims(
        app_slug=app_slug,
        reviewer_label=reviewer_label,
        issuer=effective_issuer,
        issued_at=iat,
        expires_at=exp,
        jti=jti,
    )

    # 5. Build header
    kid = f"rac-app-{app_slug}-v1"
    header: dict[str, object] = {"alg": "ES256", "typ": "JWT", "kid": kid}

    # 6. Build signing input
    signing_input_str, signing_input_bytes = build_signing_input(header, claims)

    # 7. Compute SHA-256 digest
    digest = hashlib.sha256(signing_input_bytes).digest()

    # 8. Sign
    if signer is None:
        signer = _build_kv_signer(app_slug, settings.kv_uri)

    sig_bytes = await signer(digest)

    # 9. Normalise to raw r||s if format is DER
    if signature_format is None:
        # If no override: try to use detected format; fall back to RAW_R_S
        try:
            from rac_control_plane.services.tokens.key_probe import get_detected_format
            signature_format = get_detected_format()
        except RuntimeError:
            signature_format = SignatureFormat.RAW_R_S

    raw_sig = der_to_raw_r_s(sig_bytes) if signature_format == SignatureFormat.DER else sig_bytes

    # 10. Assemble JWS
    jws = assemble_jws(signing_input_str, raw_sig)

    # 11. INSERT reviewer_token row
    token_row = ReviewerToken(
        id=jti,
        principal_id=actor_principal_id,  # legacy field kept for compat
        jti=str(jti),
        app_id=app_id,
        reviewer_label=reviewer_label,
        kid=kid,
        issued_by_principal_id=actor_principal_id,
        expires_at=exp,
        scope="read",
    )
    session.add(token_row)
    await session.flush()

    # 12. INSERT approval_event
    event = ApprovalEvent(
        submission_id=None,
        kind="reviewer_token_issued",
        actor_principal_id=actor_principal_id,
        payload={"jti": str(jti), "reviewer_label": reviewer_label, "app_id": str(app_id)},
    )
    session.add(event)
    await session.flush()

    logger.info(
        "reviewer_token_issued",
        jti=str(jti),
        app_id=str(app_id),
        reviewer_label=reviewer_label,
        expires_at=exp.isoformat(),
    )

    return IssuedToken(
        jwt=jws,
        jti=jti,
        expires_at=exp,
        reviewer_label=reviewer_label,
    )


def _build_kv_signer(
    app_slug: str,
    kv_uri: str,
) -> Callable[[bytes], Awaitable[bytes]]:
    """Build a production Key Vault async signer for the given app slug.

    Uses azure-identity DefaultAzureCredential and azure-keyvault-keys
    CryptographyClient.sign(ES256, digest).
    """
    from azure.identity.aio import DefaultAzureCredential  # type: ignore[import-untyped]
    from azure.keyvault.keys.aio import KeyClient  # type: ignore[import-untyped]
    from azure.keyvault.keys.crypto.aio import CryptographyClient  # type: ignore[import-untyped]
    from azure.keyvault.keys.crypto import SignatureAlgorithm  # type: ignore[import-untyped]

    key_name = f"rac-app-{app_slug}-v1"

    async def _sign(digest: bytes) -> bytes:
        credential = DefaultAzureCredential()
        try:
            key_client = KeyClient(vault_url=kv_uri, credential=credential)
            key = await key_client.get_key(key_name)
            crypto_client = CryptographyClient(key=key, credential=credential)
            result = await crypto_client.sign(SignatureAlgorithm.es256, digest)
            return result.signature
        finally:
            await credential.close()

    return _sign
