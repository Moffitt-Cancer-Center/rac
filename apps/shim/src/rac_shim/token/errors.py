# pattern: Functional Core


class TokenInvalid(Exception):
    """Base class. Subclasses carry `code` (stable string) and `internal_detail`
    (never shown to users — AC7.4)."""

    code: str = "invalid"

    def __init__(self, internal_detail: str = "") -> None:
        super().__init__(internal_detail)
        self.internal_detail = internal_detail


class Expired(TokenInvalid):
    code = "expired"


class WrongAudience(TokenInvalid):
    code = "wrong_audience"


class WrongIssuer(TokenInvalid):
    code = "wrong_issuer"


class SignatureInvalid(TokenInvalid):
    code = "signature_invalid"


class Malformed(TokenInvalid):
    code = "malformed"


class Revoked(TokenInvalid):
    code = "revoked"


class NotYetValid(TokenInvalid):
    code = "not_yet_valid"
