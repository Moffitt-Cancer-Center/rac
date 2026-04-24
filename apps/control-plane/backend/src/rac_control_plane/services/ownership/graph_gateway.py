# pattern: Imperative Shell
"""Microsoft Graph user-lookup gateway.

Wraps msgraph-sdk calls with:
- Positive-result TTL cache (default 300 s).
- None return for 404 (user not found) — never raises on missing user.
- 429 retry with exponential back-off.
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import structlog
from cachetools import TTLCache
from msgraph import GraphServiceClient  # type: ignore[attr-defined]
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from rac_control_plane.provisioning.credentials import get_graph_client
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

# Module-level positive-result cache.  Keyed by OID, value is GraphUser.
# Size is capped at 10_000 entries to bound memory under heavy batch loads.
_user_cache: TTLCache["UUID", "GraphUser"] = TTLCache(
    maxsize=10_000,
    ttl=300,  # replaced at module init via _refresh_cache_ttl()
)


def _refresh_cache_ttl() -> None:
    """Recreate the cache with the TTL from current settings.

    Called lazily on first use so that Settings is already populated.
    """
    global _user_cache  # noqa: PLW0603
    try:
        ttl = get_settings().graph_user_cache_ttl_seconds
    except Exception:
        ttl = 300
    _user_cache = TTLCache(maxsize=10_000, ttl=ttl)


@dataclass(frozen=True)
class GraphUser:
    """Minimal Entra user representation fetched from Microsoft Graph."""

    oid: UUID
    account_enabled: bool
    display_name: str | None
    user_principal_name: str | None
    department: str | None


async def get_user(
    oid: UUID,
    *,
    client: GraphServiceClient | None = None,
) -> GraphUser | None:
    """Fetch a single user from Microsoft Graph by OID.

    Returns None if the user does not exist (HTTP 404) rather than raising.
    Retries up to 3 times on HTTP 429 (throttle) with exponential back-off.

    Args:
        oid: Entra object ID of the user.
        client: Optional pre-built GraphServiceClient.  Defaults to the
                module-level singleton from credentials.py.

    Returns:
        GraphUser if found and cache is populated; None if user not found.
    """
    if oid in _user_cache:
        return _user_cache[oid]

    effective_client = client or get_graph_client()
    max_retries = 3
    backoff = 1.0

    for attempt in range(max_retries):
        try:
            user = await effective_client.users.by_user_id(str(oid)).get()
            if user is None:
                return None

            graph_user = GraphUser(
                oid=oid,
                account_enabled=bool(user.account_enabled),
                display_name=user.display_name,
                user_principal_name=user.user_principal_name,
                department=user.department,
            )
            _user_cache[oid] = graph_user
            return graph_user

        except ODataError as exc:
            status = (
                exc.response_status_code
                if hasattr(exc, "response_status_code")
                else None
            )
            if status == 404:
                return None
            if status == 429:
                retry_after = backoff * (2**attempt)
                logger.warning(
                    "graph_429_throttle_backoff",
                    oid=str(oid),
                    attempt=attempt,
                    retry_after=retry_after,
                )
                await asyncio.sleep(retry_after)
                continue
            logger.error(
                "graph_user_lookup_error", oid=str(oid), error=str(exc)
            )
            raise

    return None


async def get_users_batch(
    oids: list[UUID],
    *,
    client: GraphServiceClient | None = None,
) -> dict[UUID, GraphUser | None]:
    """Batch lookup of multiple users from Microsoft Graph.

    Uses individual parallel lookups backed by the in-process cache.
    Returns a dict keyed by OID; missing users map to None.

    Args:
        oids: List of Entra object IDs.
        client: Optional pre-built GraphServiceClient.

    Returns:
        Dict mapping each OID to a GraphUser or None.
    """
    if not oids:
        return {}

    effective_client = client or get_graph_client()
    tasks = [get_user(oid, client=effective_client) for oid in oids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[UUID, GraphUser | None] = {}
    for oid, result in zip(oids, results, strict=False):
        if isinstance(result, BaseException):
            logger.error(
                "graph_batch_user_error", oid=str(oid), error=str(result)
            )
            out[oid] = None
        else:
            out[oid] = result

    return out
