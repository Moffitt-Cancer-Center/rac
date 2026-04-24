# pattern: Imperative Shell
"""GitHub repository_dispatch client for rac-pipeline triggering.

Sends a repository_dispatch event to the rac-pipeline repo so GitHub Actions
picks it up and runs the build-and-scan workflow.

References:
- https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event
- GitHub documents a 10 KB limit for client_payload JSON.
"""

import asyncio
import json
from typing import Any

import httpx
import structlog

from rac_control_plane.errors import ValidationApiError

logger = structlog.get_logger(__name__)

MAX_PAYLOAD_BYTES = 10_000  # GitHub's documented client_payload size cap

_GH_API_BASE = "https://api.github.com"
_DISPATCH_EVENT_TYPE = "rac_submission"
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds; doubles per retry


class PipelineDispatchError(Exception):
    """Raised when dispatch cannot proceed.

    Covers: 4xx responses from GitHub, payload-too-large, retry exhaustion.
    Does NOT expose raw GitHub response bodies to callers.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


async def dispatch(
    owner: str,
    repo: str,
    payload: dict[str, Any],
    *,
    auth_token: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> None:
    """POST a repository_dispatch event to GitHub to trigger rac-pipeline.

    Args:
        owner: GitHub org or user that owns the pipeline repo.
        repo: Pipeline repo name (typically "rac-pipeline").
        payload: The client_payload dict that the workflow will receive.
        auth_token: GitHub App installation token or PAT for authentication.
        client: Optional pre-built httpx.AsyncClient for testing.
        timeout: Per-request timeout in seconds.

    Raises:
        ValidationApiError('pipeline_payload_too_large', ...):
            Serialized client_payload exceeds MAX_PAYLOAD_BYTES.
            No HTTP call is attempted in this case.
        PipelineDispatchError:
            On 4xx responses (including 401, 404, 422) or after retry
            exhaustion on 5xx / network errors.
    """
    # Size check BEFORE any I/O — fail fast for definitively-invalid payloads
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_bytes = len(payload_json.encode("utf-8"))
    if payload_bytes > MAX_PAYLOAD_BYTES:
        raise ValidationApiError(
            code="pipeline_payload_too_large",
            public_message=(
                f"Pipeline dispatch payload is {payload_bytes} bytes, "
                f"exceeding GitHub's {MAX_PAYLOAD_BYTES}-byte limit. "
                "Reduce the manifest size."
            ),
        )

    url = f"{_GH_API_BASE}/repos/{owner}/{repo}/dispatches"
    body = {"event_type": _dispatch_event_type(), "client_payload": payload}
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rac-control-plane/1.0",
        "Content-Type": "application/json",
    }

    own_client = client is None
    _client: httpx.AsyncClient = (
        httpx.AsyncClient(timeout=timeout) if own_client else client  # type: ignore[assignment]
    )

    try:
        await _dispatch_with_retry(
            _client, url, body, headers, timeout=timeout
        )
    finally:
        if own_client:
            await _client.aclose()


def _dispatch_event_type() -> str:
    """Return the repository_dispatch event type string.

    Isolated into its own function so it's easy to see in tests
    without having to parse the request body.
    """
    return _DISPATCH_EVENT_TYPE


async def _dispatch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> None:
    """POST with exponential-backoff retry on 5xx and network errors."""
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.post(url, json=body, headers=headers)

            if response.status_code == 204:
                # Success — GitHub returns 204 No Content on accepted dispatch
                logger.info("pipeline_dispatch_success", url=url, attempt=attempt)
                return

            if response.status_code >= 500:
                # Server error — retry
                logger.warning(
                    "pipeline_dispatch_server_error",
                    status=response.status_code,
                    attempt=attempt,
                    max_retries=_MAX_RETRIES,
                )
                last_exc = PipelineDispatchError(
                    f"GitHub returned {response.status_code}",
                    status=response.status_code,
                )
                # Exponential backoff: 1s, 2s, 4s, …
                await asyncio.sleep(_BACKOFF_BASE * (2**attempt))
                continue

            # 4xx — non-retryable; do not surface raw GH body
            logger.error(
                "pipeline_dispatch_client_error",
                status=response.status_code,
                attempt=attempt,
            )
            raise PipelineDispatchError(
                "GitHub rejected the dispatch request",
                status=response.status_code,
            )

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "pipeline_dispatch_network_error",
                error=str(exc),
                attempt=attempt,
                max_retries=_MAX_RETRIES,
            )
            last_exc = PipelineDispatchError(
                f"network error reaching GitHub: {type(exc).__name__}",
            )
            await asyncio.sleep(_BACKOFF_BASE * (2**attempt))

    # All retries exhausted
    raise PipelineDispatchError(
        f"dispatch failed after {_MAX_RETRIES} attempts"
    ) from last_exc
