# pattern: Imperative Shell
"""Azure DNS provisioning wrapper.

Creates or updates an A record for a researcher app's subdomain.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


async def upsert_a_record(
    zone_name: str,
    subdomain: str,
    ip_address: str,
    tags: dict[str, str],
    *,
    dns_client: Any = None,
) -> str:
    """Create or update a DNS A record. Idempotent.

    Args:
        zone_name: The DNS zone name (e.g. 'rac.example.com').
        subdomain: The relative subdomain (e.g. 'myapp').
        ip_address: IPv4 address to point the record at.
        tags: AC11.1 tags — passed as metadata on the record set.
        dns_client: Optional injected DNS management client for testing.

    Returns:
        The A record set resource ID.

    Raises:
        TransientProvisioningError: On 429/5xx.
        ProvisioningError: On permanent errors.
    """
    from azure.core.exceptions import HttpResponseError
    from azure.mgmt.dns import DnsManagementClient
    from azure.mgmt.dns.models import ARecord, RecordSet

    from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError

    settings = get_settings()

    if dns_client is None:
        from rac_control_plane.provisioning.credentials import get_azure_credential
        credential = get_azure_credential()
        dns_client = DnsManagementClient(
            credential=credential,
            subscription_id=settings.subscription_id,
        )

    try:
        result = await asyncio.to_thread(
            lambda: dns_client.record_sets.create_or_update(
                resource_group_name=settings.resource_group,
                zone_name=zone_name,
                relative_record_set_name=subdomain,
                record_type="A",
                parameters=RecordSet(
                    ttl=3600,
                    a_records=[ARecord(ipv4_address=ip_address)],
                    metadata=tags,
                ),
            )
        )
        resource_id: str = result.id or ""
        logger.info("dns_a_record_upserted", subdomain=subdomain, zone=zone_name, ip=ip_address)
        return resource_id

    except HttpResponseError as exc:
        status: int = (exc.response.status_code if exc.response else None) or 0
        msg = str(exc.error.message if exc.error else exc)[:200]

        if status in (429, 500, 502, 503, 504):
            raise TransientProvisioningError(
                code="dns_transient",
                detail=f"DNS HTTP {status}: {msg}",
            ) from exc

        if status == 409:
            raise ProvisioningError(
                code="dns_conflict",
                detail=f"DNS conflict upserting {subdomain}.{zone_name}: {msg}",
                retryable=False,
            ) from exc

        if 400 <= status < 500:
            raise ProvisioningError(
                code="dns_error",
                detail=f"DNS error {status} for {subdomain}: {msg}",
                retryable=False,
            ) from exc

        raise TransientProvisioningError(
            code="dns_transient",
            detail=f"DNS unexpected error {status}: {msg}",
        ) from exc
