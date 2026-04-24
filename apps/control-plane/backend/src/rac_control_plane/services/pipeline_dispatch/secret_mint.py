# pattern: Imperative Shell
"""Generate and store a short-lived callback HMAC secret in Azure Key Vault.

The secret is used by the pipeline to sign its callback POST.  The Control
Plane fetches the secret from Key Vault when a callback arrives, so the
secret value never travels through the Control Plane's response body.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from azure.keyvault.secrets.aio import SecretClient

logger = structlog.get_logger(__name__)


async def mint_callback_secret(
    submission_id: UUID,
    *,
    kv_uri: str,
    expiry_minutes: int,
    client: SecretClient | None = None,
) -> tuple[str, str]:
    """Generate 32 random bytes as hex, store in Key Vault with expiry.

    Args:
        submission_id: UUID of the submission this secret guards.
        kv_uri: Azure Key Vault URI (e.g. "https://rac-kv.vault.azure.net/").
        expiry_minutes: Seconds until the secret expires (2 × pipeline timeout
            is a good default so late callbacks are still valid).
        client: Optional pre-constructed SecretClient for testing. When None,
            a DefaultAzureCredential-backed client is created automatically.

    Returns:
        (secret_name, secret_value_hex) — callers use the name to reference
        the secret in the dispatch payload; the pipeline fetches the value
        directly from Key Vault using GHA OIDC.

    Raises:
        azure.core.exceptions.AzureError: on Key Vault connectivity or auth failure.
    """
    secret_name = f"rac-pipeline-cb-{submission_id}"
    secret_value = secrets.token_hex(32)  # 32 bytes → 64 hex chars

    expiry = datetime.now(tz=UTC) + timedelta(minutes=expiry_minutes)

    if client is None:
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
        _client: SecretClient = SecretClient(vault_url=kv_uri, credential=credential)
        own_client = True
    else:
        _client = client
        own_client = False

    try:
        await _client.set_secret(
            secret_name,
            secret_value,
            expires_on=expiry,
            content_type="text/plain",
        )

        logger.info(
            "callback_secret_minted",
            submission_id=str(submission_id),
            secret_name=secret_name,
            expires_at=expiry.isoformat(),
        )
    finally:
        if own_client:
            await _client.close()

    return secret_name, secret_value
