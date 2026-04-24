# pattern: Imperative Shell
"""Inbound pipeline callback webhook endpoint.

POST /webhooks/pipeline-callback/{submission_id}

Validates the HMAC signature, parses the payload, ingests the scan result,
and advances the submission FSM.  All failure responses use 401 to avoid
leaking submission IDs to an unauthenticated caller.
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.webhooks import PipelineCallback
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import Submission
from rac_control_plane.metrics import scan_verdict_counter
from rac_control_plane.services.scan_results.ingest import ingest
from rac_control_plane.services.submissions.fsm import InvalidTransitionError
from rac_control_plane.services.webhooks.deliver import deliver_event
from rac_control_plane.services.webhooks.verify import SignatureInvalid, verify_signature
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["webhooks"])

_401 = Response(
    status_code=401,
    content=b'{"code":"unauthorized","message":"invalid or missing credentials"}',
    media_type="application/json",
)


@router.post("/webhooks/pipeline-callback/{submission_id}", status_code=200)
async def pipeline_callback(
    submission_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Receive a signed callback from the rac-pipeline on scan completion.

    Security:
    - Raw body is read first; JSON is never parsed before HMAC verification.
    - Returns 401 for missing headers, unknown submission, missing secret, or
      invalid signature — leaking no information about which check failed.
    - Timestamp must be within 5 minutes (both past and 60s future).
    """
    # Read raw bytes — the signature covers the exact wire bytes
    body = await request.body()

    # Extract required signature headers
    ts_header = request.headers.get("X-RAC-Timestamp")
    sig_header = request.headers.get("X-RAC-Signature-256")
    if not ts_header or not sig_header:
        logger.warning("webhook_missing_headers", submission_id=str(submission_id))
        return _401

    # Load submission — 401 (not 404) to avoid leaking submission existence
    stmt = select(Submission).where(Submission.id == submission_id)
    result = await session.execute(stmt)
    submission = result.scalar_one_or_none()
    if submission is None:
        logger.warning("webhook_submission_not_found", submission_id=str(submission_id))
        return _401

    # Fetch callback secret from Key Vault
    secret_bytes = await _fetch_callback_secret(submission_id)
    if secret_bytes is None:
        logger.warning("webhook_secret_not_found", submission_id=str(submission_id))
        return _401

    # Verify HMAC signature + timestamp freshness
    try:
        verify_signature(sig_header, secret_bytes, ts_header, body)
    except SignatureInvalid as exc:
        logger.warning(
            "webhook_signature_invalid",
            submission_id=str(submission_id),
            reason=str(exc),
        )
        return Response(
            status_code=401,
            content=b'{"code":"invalid_signature","message":"signature verification failed"}',
            media_type="application/json",
        )

    # Parse callback body — only after successful verification
    try:
        callback = PipelineCallback.model_validate_json(body)
    except Exception as exc:
        logger.error("webhook_parse_error", submission_id=str(submission_id), exc=str(exc))
        return Response(
            status_code=400,
            content=b'{"code":"bad_request","message":"invalid callback payload"}',
            media_type="application/json",
        )

    # Build metric emitter
    def _emit_metric(verdict: str) -> None:
        scan_verdict_counter.add(1, {"verdict": verdict})

    # Build KV purge helper
    async def _purge_kv(secret_name: str) -> None:
        await _delete_callback_secret(secret_name)

    # Ingest — may raise InvalidTransitionError
    try:
        await ingest(
            session,
            submission,
            callback,
            metric_emitter=_emit_metric,
            deliver_events=deliver_event,
            kv_purge=_purge_kv,
        )
    except InvalidTransitionError as exc:
        logger.warning(
            "webhook_invalid_transition",
            submission_id=str(submission_id),
            current=str(submission.status),
            error=str(exc),
        )
        return Response(
            status_code=409,
            content=b'{"code":"invalid_transition","message":"submission in unexpected state"}',
            media_type="application/json",
        )

    return Response(status_code=200)


async def _fetch_callback_secret(submission_id: UUID) -> bytes | None:
    """Fetch the pipeline callback HMAC secret from Key Vault.

    Returns None if the secret does not exist (expired or never minted).
    """
    secret_name = f"rac-pipeline-cb-{submission_id}"
    settings = get_settings()

    try:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.kv_uri, credential=credential)
        try:
            secret = await client.get_secret(secret_name)
            return secret.value.encode() if secret.value else None
        except ResourceNotFoundError:
            return None
        finally:
            await client.close()
            await credential.close()
    except Exception:
        logger.exception("kv_fetch_error", secret_name=secret_name)
        return None


async def _delete_callback_secret(secret_name: str) -> None:
    """Soft-delete the callback secret from Key Vault (single-use cleanup)."""
    settings = get_settings()
    try:
        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.kv_uri, credential=credential)
        try:
            await client.begin_delete_secret(secret_name)  # type: ignore[attr-defined]
        finally:
            await client.close()
            await credential.close()
    except Exception:
        logger.warning("kv_purge_error", secret_name=secret_name)
