# pattern: Functional Core

import base64
import json
from datetime import datetime, timezone
from uuid import UUID

from joserfc import jwt
from joserfc.errors import BadSignatureError, DecodeError
from joserfc.jwk import ECKey

from rac_shim.token.claims import RacTokenClaims
from rac_shim.token.errors import (
    Expired,
    Malformed,
    NotYetValid,
    SignatureInvalid,
    WrongAudience,
    WrongIssuer,
)


def decode_unverified_header(token: str) -> dict[str, object]:
    """Parse the JWT header without signature verification.

    Returns a dict with at minimum the algorithm fields (e.g. 'alg', 'kid').
    Raises Malformed if the token is not a structurally valid JWT.
    """
    parts = token.split(".")
    if len(parts) != 3:  # noqa: PLR2004
        raise Malformed(f"Expected 3 JWT parts, got {len(parts)}")
    try:
        # Add padding so standard base64 decode works
        padded = parts[0] + "=" * (-len(parts[0]) % 4)
        header_bytes = base64.urlsafe_b64decode(padded)
        return dict(json.loads(header_bytes))  # type: ignore[arg-type]
    except Exception as exc:
        raise Malformed(f"Cannot decode JWT header: {exc}") from exc


def verify_signature_and_claims(
    token: str,
    *,
    public_key: ECKey,
    expected_issuer: str,
    expected_audience: str,
    now: datetime,
) -> RacTokenClaims:
    """Verify the token signature and all required claims.

    Raises the appropriate TokenInvalid subclass or returns RacTokenClaims.

    Claim validation order:
    1. Decode header (raises Malformed on format errors).
    2. Decode payload + verify signature. BadSignatureError → SignatureInvalid;
       DecodeError → Malformed.
    3. Check iss == expected_issuer else WrongIssuer.
    4. Check aud == expected_audience (exact string) else WrongAudience.
    5. Check exp > now else Expired.
    6. If nbf present and nbf > now → NotYetValid.
    7. Build RacTokenClaims.
    """
    # Step 1: structural check
    decode_unverified_header(token)

    # Step 2: signature verification
    try:
        tok = jwt.decode(token, public_key, algorithms=["ES256"])
    except BadSignatureError as exc:
        raise SignatureInvalid(str(exc)) from exc
    except DecodeError as exc:
        raise Malformed(str(exc)) from exc
    except Exception as exc:
        # Catch any other joserfc errors (e.g. algorithm mismatch) as Malformed
        raise Malformed(str(exc)) from exc

    claims = tok.claims

    # Step 3: issuer
    if claims.get("iss") != expected_issuer:
        raise WrongIssuer(f"iss={claims.get('iss')!r} expected={expected_issuer!r}")

    # Step 4: audience (exact string match — AC7.6)
    if claims.get("aud") != expected_audience:
        raise WrongAudience(f"aud={claims.get('aud')!r} expected={expected_audience!r}")

    # Step 5: expiry
    exp_raw = claims.get("exp")
    if exp_raw is None:
        raise Malformed("Missing 'exp' claim")
    exp_dt = datetime.fromtimestamp(int(exp_raw), tz=timezone.utc)
    if exp_dt <= now:
        raise Expired(f"exp={exp_dt.isoformat()} now={now.isoformat()}")

    # Step 6: not-before
    nbf_dt: datetime | None = None
    nbf_raw = claims.get("nbf")
    if nbf_raw is not None:
        nbf_dt = datetime.fromtimestamp(int(nbf_raw), tz=timezone.utc)
        if nbf_dt > now:
            raise NotYetValid(f"nbf={nbf_dt.isoformat()} now={now.isoformat()}")

    # Step 7: build claims object
    iat_raw = claims.get("iat")
    if iat_raw is None:
        raise Malformed("Missing 'iat' claim")
    iat_dt = datetime.fromtimestamp(int(iat_raw), tz=timezone.utc)

    jti_raw = claims.get("jti")
    if jti_raw is None:
        raise Malformed("Missing 'jti' claim")
    try:
        jti = UUID(str(jti_raw))
    except ValueError as exc:
        raise Malformed(f"Invalid 'jti' UUID: {jti_raw!r}") from exc

    return RacTokenClaims(
        iss=str(claims.get("iss", "")),
        aud=str(claims.get("aud", "")),
        sub=str(claims.get("sub", "")),
        jti=jti,
        iat=iat_dt,
        exp=exp_dt,
        nbf=nbf_dt,
        scope=str(claims["scope"]) if "scope" in claims else "read",
    )
