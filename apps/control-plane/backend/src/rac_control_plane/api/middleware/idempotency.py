# pattern: Imperative Shell
"""Idempotency-Key middleware for Postgres-backed duplicate detection.

On mutating requests (POST, PUT, DELETE, PATCH), reads the Idempotency-Key header
and stores request/response pairs keyed on (key, principal_id).

Same key + same body = replay cached response + X-Idempotent-Replay: true
Same key + different body = 422 Unprocessable Entity
No key = request proceeds normally (caller opted out)
No principal = return 401 (defer to auth layer)

Design: BaseHTTPMiddleware approach, reads body once, replays to downstream.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from rac_control_plane.data.models import IdempotencyKey
from rac_control_plane.services.idempotency import hash_request, validate_key


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """ASGI middleware for Idempotency-Key handling.

    Takes an async_sessionmaker so it can open its own DB session
    independently of the per-request dependency injection.
    """

    def __init__(self, app: object, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._session_factory = session_factory

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process the request, applying idempotency semantics."""
        method = request.method
        is_mutating = method in ("POST", "PUT", "DELETE", "PATCH")

        if not is_mutating:
            return await call_next(request)

        idempotency_key = request.headers.get("idempotency-key")
        if not idempotency_key:
            # No key — caller opted out, proceed normally
            return await call_next(request)

        # Validate key format
        if not validate_key(idempotency_key):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_idempotency_key",
                    "message": "Idempotency-Key must be a UUID or alphanumeric string ≤ 256 chars",
                    "correlation_id": "",
                },
            )

        # Get principal_id from request state. Auth middleware (CorrelationIdMiddleware
        # runs before this, but current_principal is a Depends so it runs per-route.
        # We read the raw body and hash it; then defer principal resolution to the route.
        # If no principal (no auth header), return 401.
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "code": "unauthorized",
                    "message": "Authentication required for idempotent requests.",
                    "correlation_id": "",
                },
            )

        # Extract principal_id from state (set after auth runs).
        # Since auth is a Depends and hasn't run yet at middleware time, we use
        # a heuristic: decode the token lightly to get the oid/appid for keying.
        # Full validation happens at the route level. If decode fails, pass through.
        import jwt as pyjwt

        raw_token = auth_header[len("Bearer "):]
        try:
            raw_claims: dict[str, object] = pyjwt.decode(
                raw_token,
                options={"verify_signature": False},
                algorithms=["HS256", "RS256"],
            )
        except pyjwt.PyJWTError:
            # Malformed token — let the auth layer reject it
            return await call_next(request)

        oid = raw_claims.get("oid") or raw_claims.get("appid")
        if not oid:
            # Token has no identity claim — let auth handle it
            return await call_next(request)

        try:
            principal_uuid = UUID(str(oid))
        except (ValueError, AttributeError):
            return await call_next(request)

        # Read body once
        body = await request.body()
        request_hash = hash_request(method, str(request.url.path), body)

        async with self._session_factory() as session:
            # Try to insert the idempotency record
            new_record = IdempotencyKey(
                key=idempotency_key,
                principal_id=principal_uuid,
                request_hash=request_hash,
                response_status=0,
                response_body="",
                response_headers={},
            )
            try:
                session.add(new_record)
                await session.flush()
                await session.commit()
            except IntegrityError:
                await session.rollback()
                # Key already exists — check if hash matches
                stmt = select(IdempotencyKey).where(
                    (IdempotencyKey.key == idempotency_key)
                    & (IdempotencyKey.principal_id == principal_uuid)
                )
                existing = await session.scalar(stmt)

                if existing is None:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "code": "idempotency_error",
                            "message": "Idempotency-Key handling failed",
                            "correlation_id": "",
                        },
                    )

                if existing.request_hash != request_hash:
                    return JSONResponse(
                        status_code=422,
                        content={
                            "code": "idempotency_key_reused",
                            "message": (
                                f"Idempotency-Key '{idempotency_key}' "
                                "reused with different request body"
                            ),
                            "correlation_id": "",
                        },
                    )

                # Same key + same body → replay cached response
                if existing.response_status == 0:
                    # Race: another request is still in-flight — proceed
                    pass
                else:
                    resp_headers: dict[str, str] = dict(existing.response_headers or {})
                    resp_headers["x-idempotent-replay"] = "true"
                    response_body_bytes = existing.response_body.encode("utf-8")

                    # AC3.2: replay always returns HTTP 200 (not the original status)
                    # to signal a deduplicated response vs. a new creation (201).
                    return Response(
                        content=response_body_bytes,
                        status_code=200,
                        headers=resp_headers,
                        media_type=resp_headers.get("content-type", "application/json"),
                    )

            # New record inserted — call downstream and capture the response
            # We need to replay the body to the downstream handler
            async def receive_with_body() -> dict[str, object]:
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }

            # Swap receive with body replay so downstream handler sees the body
            from starlette.types import Message

            original_receive = request.receive

            body_consumed = False

            async def replay_receive() -> Message:
                nonlocal body_consumed
                if not body_consumed:
                    body_consumed = True
                    return {
                        "type": "http.request",
                        "body": body,
                        "more_body": False,
                    }
                return await original_receive()

            # Override receive on the request scope via scope dict (type-safe)
            request.scope["_receive_override"] = replay_receive
            # Patch the private attribute which Starlette reads
            object.__setattr__(request, "_receive", replay_receive)

            response = await call_next(request)

            # Capture response body
            resp_body = b""
            async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                resp_body += chunk if isinstance(chunk, bytes) else chunk.encode()

            # Store response in DB
            resp_headers_dict: dict[str, str] = dict(response.headers)
            async with self._session_factory() as update_session:
                stmt2 = select(IdempotencyKey).where(
                    (IdempotencyKey.key == idempotency_key)
                    & (IdempotencyKey.principal_id == principal_uuid)
                )
                record = await update_session.scalar(stmt2)
                if record is not None:
                    record.response_status = response.status_code
                    record.response_body = resp_body.decode("utf-8", errors="replace")
                    record.response_headers = resp_headers_dict
                    await update_session.commit()

            return Response(
                content=resp_body,
                status_code=response.status_code,
                headers=resp_headers_dict,
                media_type=str(response.media_type) if response.media_type else None,
            )
