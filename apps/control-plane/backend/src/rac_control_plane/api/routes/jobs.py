# pattern: Imperative Shell
"""Internal ACA scheduled-job endpoints.

These endpoints are NOT authenticated via Entra OIDC.  Instead they require
an ``X-Internal-Auth`` header matching ``settings.internal_job_secret``.
They return 404 (not 401/403) on missing or wrong header to avoid advertising
the existence of internal endpoints to external callers.

# ACA Scheduled Job configuration (docs-only comment)
# ─────────────────────────────────────────────────────
# Job name:     rotate-webhook-secrets
# Cron expr:    0 2 * * *   (2 AM UTC daily)
# Container:    <same image as control-plane>
# Command:      curl -sf -X POST http://control-plane/internal/jobs/rotate-webhook-secrets
#                  -H "X-Internal-Auth: <internal_job_secret value>"
# Replica count: 1
# Timeout:      300s
#
# The ACA job runs to completion; no persistent container is needed.
# Deploy with:
#   az containerapp job create \\
#     --name rotate-webhook-secrets \\
#     --resource-group <rg> \\
#     --environment <aca-env> \\
#     --trigger-type Schedule \\
#     --cron-expression "0 2 * * *" \\
#     --image <acr>/control-plane:<tag> \\
#     --command "curl" "-sf" "-X" "POST" \\
#       "http://control-plane/internal/jobs/rotate-webhook-secrets" \\
#       "-H" "X-Internal-Auth: ..." \\
#     --secrets "internal-job-secret=<value>" \\
#     --env-vars "RAC_INTERNAL_JOB_SECRET=secretref:internal-job-secret"
"""

import hmac

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.db import get_session
from rac_control_plane.services.webhooks.rotate_secrets import rotate_expiring_secrets
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/jobs", tags=["internal"])

_404 = Response(status_code=404)


def _check_internal_auth(request: Request) -> bool:
    """Return True if the X-Internal-Auth header matches the configured secret."""
    settings = get_settings()
    if settings.internal_job_secret is None:
        return False

    provided = request.headers.get("X-Internal-Auth", "")
    expected = settings.internal_job_secret.get_secret_value()
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(provided.encode(), expected.encode())


@router.post("/rotate-webhook-secrets", include_in_schema=False)
async def rotate_webhook_secrets(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Rotate HMAC secrets for webhook subscriptions past the rotation threshold.

    Protected by ``X-Internal-Auth`` header; returns 404 on missing / wrong value.
    Called by an ACA scheduled job (see module docstring for job definition).
    """
    if not _check_internal_auth(request):
        return _404  # type: ignore[return-value]

    settings = get_settings()
    rotated = await rotate_expiring_secrets(
        session,
        rotation_days=settings.webhook_secret_rotation_days,
    )

    logger.info("webhook_secrets_rotated", count=len(rotated))
    return JSONResponse({"rotated": [str(uid) for uid in rotated]})
