# pattern: Functional Core


def expected_audience_for_host(host: str, parent_domain: str) -> str | None:
    """Given host 'foo.rac.example.org' and parent_domain 'rac.example.org',
    returns 'rac-app:foo'.

    Handles:
    - Case-insensitive matching (host may arrive as 'FOO.Rac.Example.ORG').
    - Trailing dot stripping ('foo.rac.example.org.' → 'foo.rac.example.org').
    - Port number stripping ('foo.rac.example.org:443' → 'foo.rac.example.org').
    - Host that is not '<slug>.<parent_domain>' → None.
    - Empty slug ('.rac.example.org') → None.
    - Multi-segment slug ('foo.bar.rac.example.org'): the RAC design uses
      single-segment slugs only. A multi-segment slug would produce a
      slug string that contains dots, which is invalid, so this returns None.
    """
    # Normalise: lowercase, strip trailing dot, strip port
    h = host.lower()
    h = h.rstrip(".")
    if ":" in h:
        h = h.rsplit(":", 1)[0]

    pd = parent_domain.lower().rstrip(".")

    suffix = "." + pd
    if not h.endswith(suffix):
        return None

    slug = h[: -len(suffix)]
    if not slug:
        return None

    # Multi-segment slug (contains a dot) → None
    if "." in slug:
        return None

    return f"rac-app:{slug}"
