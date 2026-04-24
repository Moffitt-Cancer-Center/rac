"""Tests for rac_shim.token.audience — pure host-to-audience mapping."""

import pytest

from rac_shim.token.audience import expected_audience_for_host

PARENT = "rac.example.com"


@pytest.mark.parametrize(
    "host, parent, expected",
    [
        # Basic match
        ("foo.rac.example.com", PARENT, "rac-app:foo"),
        # Case-insensitive host
        ("FOO.rac.example.com", PARENT, "rac-app:foo"),
        ("FOO.RAC.EXAMPLE.COM", PARENT, "rac-app:foo"),
        # Trailing dot in host
        ("foo.rac.example.com.", PARENT, "rac-app:foo"),
        # Port stripping
        ("foo.rac.example.com:443", PARENT, "rac-app:foo"),
        ("foo.rac.example.com:8080", PARENT, "rac-app:foo"),
        # No slug (host == parent domain)
        ("rac.example.com", PARENT, None),
        # Multi-segment slug → None (design uses single-segment slugs)
        ("foo.bar.rac.example.com", PARENT, None),
        # Wrong parent domain
        ("foo.other.example.com", PARENT, None),
        # Completely unrelated host
        ("localhost", PARENT, None),
        ("", PARENT, None),
        # Slug with hyphen (valid)
        ("my-app.rac.example.com", PARENT, "rac-app:my-app"),
        # Slug with numbers (valid)
        ("app42.rac.example.com", PARENT, "rac-app:app42"),
        # Empty slug via dot prefix
        (".rac.example.com", PARENT, None),
    ],
)
def test_expected_audience(host: str, parent: str, expected: str | None) -> None:
    assert expected_audience_for_host(host, parent) == expected
