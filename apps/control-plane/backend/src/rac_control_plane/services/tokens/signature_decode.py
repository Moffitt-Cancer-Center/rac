# pattern: Functional Core
"""Pure DER-to-raw-r||s signature conversion.

No I/O, no side effects. Converts Azure Key Vault DER-encoded ECDSA signatures
to the raw r||s format required by JWS ES256.
"""

from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def der_to_raw_r_s(der: bytes, coord_size: int = 32) -> bytes:
    """Convert a DER-encoded ECDSA signature to raw r||s concatenation.

    Azure Key Vault may return ECDSA signatures in DER format
    (ASN.1 SEQUENCE { INTEGER r, INTEGER s }) rather than the raw 64-byte
    r||s form required by JWS ES256. This function normalises the output.

    Args:
        der: DER-encoded signature bytes (ASN.1 SEQUENCE with r, s integers).
        coord_size: Byte length of each coordinate. Default 32 for P-256/ES256,
                    48 for P-384/ES384, 66 for P-521/ES512.

    Returns:
        Raw r||s as bytes of length 2 * coord_size.
    """
    r, s = decode_dss_signature(der)
    return r.to_bytes(coord_size, "big") + s.to_bytes(coord_size, "big")
