# pattern: Functional Core
"""Pure JWS (JSON Web Signature) assembly helpers.

No I/O, no side effects. Implements RFC 7515 compact serialization.
"""

import base64
import json


def base64url_encode(data: bytes) -> str:
    """RFC 7515 URL-safe Base64 without padding.

    Args:
        data: Raw bytes to encode.

    Returns:
        URL-safe Base64 string without '=' padding.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_signing_input(
    header: dict[str, object],
    payload: dict[str, object],
) -> tuple[str, bytes]:
    """Build the JWS signing input (header.payload) in canonical form.

    Uses deterministic JSON serialization (sorted keys, no whitespace) to
    ensure signature reproducibility across implementations.

    Args:
        header: JOSE header dict (e.g. {"alg": "ES256", "typ": "JWT"}).
        payload: JWT claims dict.

    Returns:
        A 2-tuple of:
        - signing_input_str: "{b64url_header}.{b64url_payload}" as str
        - signing_input_bytes: UTF-8 encoding of that string (what gets signed)
    """
    header_json = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64_header = base64url_encode(header_json)
    b64_payload = base64url_encode(payload_json)
    signing_input_str = f"{b64_header}.{b64_payload}"
    return signing_input_str, signing_input_str.encode("utf-8")


def assemble_jws(signing_input: str, signature: bytes) -> str:
    """Assemble a compact JWS token from signing input and raw signature bytes.

    Args:
        signing_input: The "{b64_header}.{b64_payload}" string.
        signature: Raw signature bytes (for ES256: 64 bytes r||s).

    Returns:
        Complete compact JWS: "{signing_input}.{b64url_signature}"
    """
    return f"{signing_input}.{base64url_encode(signature)}"
