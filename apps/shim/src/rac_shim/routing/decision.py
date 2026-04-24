# pattern: Functional Core

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from rac_shim.token.audience import expected_audience_for_host


@dataclass(frozen=True)
class AppRoute:
    slug: str
    app_id: UUID
    upstream_host: str
    access_mode: Literal["token_required", "public"]


def route_for_host(
    host: str,
    *,
    parent_domain: str,
    routes: Mapping[str, AppRoute],
) -> AppRoute | None:
    """Look up the route for a given Host header value.

    Extracts the slug from '<slug>.<parent_domain>' (case-insensitive,
    port-stripped, trailing-dot-stripped) and looks it up in the routes map.
    Returns None if the host does not match the expected pattern or the slug
    is not registered.
    """
    aud = expected_audience_for_host(host, parent_domain)
    if aud is None:
        return None
    # aud is 'rac-app:{slug}'; extract slug
    slug = aud[len("rac-app:") :]
    return routes.get(slug)
