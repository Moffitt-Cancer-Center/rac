# pattern: Imperative Shell
"""Key Vault signature format probe.

Determines at startup whether the Key Vault HSM returns ECDSA signatures in
raw r||s format (64 bytes for ES256) or DER-encoded ASN.1 format (~70-72 bytes).

The result is cached module-globally and re-read on every token issuance.
The probe re-fires only on service restart — Key Vault's signature format is a
property of the HSM firmware version, not of the individual key or request.

Runbook note: if the format changes after a Key Vault firmware upgrade, operators
must restart the Control Plane container.  Document in docs/runbooks/bootstrap.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)


class SignatureFormat(StrEnum):
    """ECDSA signature encoding format returned by Key Vault."""
    RAW_R_S = "raw_r_s"  # 64 bytes for ES256 (P-256)
    DER = "der"


# Module-level cache: set once at startup by detect_signature_format().
_detected_format: SignatureFormat | None = None

# Fixed 32-byte digest used for the format probe (all zeros — safe for probing).
_PROBE_DIGEST = bytes(32)


async def detect_signature_format(
    signer: Callable[[bytes], Awaitable[bytes]],
) -> SignatureFormat:
    """Sign a known 32-byte digest and classify the output length.

    64 bytes  → RAW_R_S (Azure SDK returns raw r||s directly).
    >64 bytes → DER (ASN.1 SEQUENCE with leading 0x30 byte, typically 70-72 bytes).

    The result is stored in the module-level ``_detected_format`` variable.
    Subsequent calls return the cached value without re-signing.

    Args:
        signer: Async callable that takes a 32-byte digest and returns signature bytes.
                In production this wraps ``CryptographyClient.sign()``.

    Returns:
        Detected SignatureFormat.
    """
    global _detected_format  # noqa: PLW0603

    if _detected_format is not None:
        return _detected_format

    sig_bytes = await signer(_PROBE_DIGEST)
    fmt = SignatureFormat.RAW_R_S if len(sig_bytes) == 64 else SignatureFormat.DER

    logger.info(
        "key_vault_signing_format_detected",
        format=fmt,
        sig_len=len(sig_bytes),
    )
    _detected_format = fmt
    return fmt


def get_detected_format() -> SignatureFormat:
    """Return the cached format.  Raises RuntimeError if detection has not run.

    Call ``detect_signature_format`` once in the FastAPI lifespan before
    serving requests.
    """
    if _detected_format is None:
        raise RuntimeError(
            "Key Vault signature format has not been detected. "
            "Call detect_signature_format() in the application lifespan."
        )
    return _detected_format


def _reset_for_tests() -> None:
    """Test-only: clear the module-level cache between test cases."""
    global _detected_format  # noqa: PLW0603
    _detected_format = None
