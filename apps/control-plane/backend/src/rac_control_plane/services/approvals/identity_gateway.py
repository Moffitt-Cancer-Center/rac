# pattern: Imperative Shell
"""Identity gateway — Graph group membership lookups.

Wraps Microsoft Graph memberOf calls with a 5-minute in-process cache.
Used to enrich Principal.roles when the JWT roles claim is not populated
(e.g., when group-based role assignment is preferred over app-role assignment).
"""

import asyncio
import logging
from uuid import UUID

from cachetools import TTLCache
from msgraph import GraphServiceClient

from rac_control_plane.provisioning.credentials import get_graph_client

logger = logging.getLogger(__name__)

# Cache: OID → frozenset of group display names / IDs
_membership_cache: TTLCache[UUID, frozenset[str]] = TTLCache(  # type: ignore[type-arg]
    maxsize=5_000,
    ttl=300,  # 5 minutes
)


async def get_principal_group_memberships(
    oid: UUID,
    *,
    client: GraphServiceClient | None = None,
) -> frozenset[str]:
    """Return the set of group IDs (object IDs) for a given principal.

    Results are cached for 5 minutes.  On error, returns an empty frozenset
    rather than raising so that the caller degrades gracefully (no role
    escalation on cache miss).

    Args:
        oid: Entra object ID of the user or service principal.
        client: Optional GraphServiceClient.  Defaults to the module singleton.

    Returns:
        frozenset of group object ID strings.  Empty if lookup fails.
    """
    if oid in _membership_cache:
        return _membership_cache[oid]

    effective_client = client or get_graph_client()
    try:
        # Use memberOf to get direct group memberships
        result = await effective_client.users.by_user_id(str(oid)).member_of.get()
        group_ids: set[str] = set()
        if result and result.value:
            for group in result.value:
                if group.id:
                    group_ids.add(group.id)
        membership = frozenset(group_ids)
        _membership_cache[oid] = membership
        return membership
    except Exception as exc:
        logger.warning(
            "graph_membership_lookup_failed",
            oid=str(oid),
            error=str(exc),
        )
        return frozenset()
