# pattern: Imperative Shell
"""Idempotency-Key middleware for Postgres-backed duplicate detection.

On mutating requests (POST, PUT, DELETE, PATCH), reads the Idempotency-Key header
and stores request/response pairs keyed on (key, principal_id).

Same key + same body = replay cached response + X-Idempotent-Replay: true
Same key + different body = 422 Unprocessable Entity
No key = request proceeds normally (caller opted out)
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rac_control_plane.data.models import IdempotencyKey
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.idempotency import hash_request, validate_key


class IdempotencyMiddleware:
    """ASGI middleware for Idempotency-Key handling."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process the request."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract method and headers
        method = scope["method"]
        is_mutating = method in ("POST", "PUT", "DELETE", "PATCH")

        if not is_mutating:
            # Pass through for non-mutating requests
            await self.app(scope, receive, send)
            return

        # Get Idempotency-Key header
        headers = dict(scope.get("headers", []))
        idempotency_key = None

        for key, value in headers.items():
            if key.lower() == b"idempotency-key":
                idempotency_key = value.decode()
                break

        if not idempotency_key:
            # No key provided, proceed normally
            await self.app(scope, receive, send)
            return

        # Validate the key
        if not validate_key(idempotency_key):
            raise ValidationApiError(
                code="invalid_idempotency_key",
                public_message="Idempotency-Key must be a UUID or alphanumeric string ≤ 256 chars",
            )

        # Get principal_id from request state (set by auth middleware)
        principal_id = getattr(scope.get("state", {}), "principal_id", None)
        if not principal_id:
            # Auth middleware not yet wired or no principal - proceed without idempotency
            await self.app(scope, receive, send)
            return

        # Read request body for hashing
        body_parts: list[bytes] = []

        async def receive_wrapper() -> Message:
            """Wrapper to capture body chunks."""
            message = await receive()
            if message["type"] == "http.request":
                body_parts.append(message.get("body", b""))
            return message

        # Collect the full body
        full_body = b""

        # Get the session from scope (will be set by dependency injection)
        session: AsyncSession | None = scope.get("state", {}).session
        if not session:
            # No session available, proceed without idempotency
            await self.app(scope, receive, send)
            return

        # First, we need to read the full body
        # We'll use a wrapped receive to capture it
        async def wrapped_receive() -> Message:
            nonlocal full_body
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                full_body += chunk
            return message

        # Compute request hash
        path = scope.get("path", "/")
        request_hash = hash_request(method, path, full_body)

        # Try to insert idempotency record (new request)
        try:
            new_record = IdempotencyKey(
                key=idempotency_key,
                principal_id=UUID(principal_id) if isinstance(principal_id, str) else principal_id,
                request_hash=request_hash,
                response_status=0,  # Placeholder, will be updated
                response_body="",  # Placeholder, will be updated
                response_headers={},  # Placeholder, will be updated
            )
            session.add(new_record)
            await session.flush()

            # This is a new request, proceed downstream
            response_started = False
            status_code = 200
            response_body_parts: list[bytes] = []
            response_headers: dict = {}

            async def send_wrapper(message: Message) -> None:
                nonlocal response_started, status_code, response_headers

                if message["type"] == "http.response.start":
                    response_started = True
                    status_code = message["status"]
                    # Capture headers
                    for raw_header_name, raw_header_value in message.get("headers", []):
                        header_name = raw_header_name.decode()
                        header_value = raw_header_value.decode()
                        response_headers[header_name] = header_value

                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    response_body_parts.append(body)

                await send(message)

            # Call downstream handler with wrapped receive/send
            # This is tricky because we've already read the body
            # We need to create a new scope/receive that replays the body

            # Create a custom receive that replays the captured body
            body_sent = False

            async def replay_receive() -> Message:
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {
                        "type": "http.request",
                        "body": full_body,
                        "more_body": False,
                    }
                return await wrapped_receive()

            await self.app(scope, replay_receive, send_wrapper)

            # Update the idempotency record with the response
            response_body = b"".join(response_body_parts)
            new_record.response_status = status_code
            new_record.response_body = response_body.decode("utf-8", errors="replace")
            new_record.response_headers = response_headers
            await session.flush()
            await session.commit()

        except IntegrityError:
            # Key already exists - check if hash matches
            await session.rollback()

            principal_uuid = (
                UUID(principal_id)
                if isinstance(principal_id, str)
                else principal_id
            )
            stmt = select(IdempotencyKey).where(
                (IdempotencyKey.key == idempotency_key)
                & (IdempotencyKey.principal_id == principal_uuid)
            )
            existing = await session.scalar(stmt)

            if existing is None:
                # Shouldn't happen, but handle gracefully
                raise ValidationApiError(
                    code="idempotency_key_error",
                    public_message="Idempotency-Key handling failed",
                ) from None

            if existing.request_hash != request_hash:
                # Same key, different request body
                msg = (
                    f"Idempotency-Key '{idempotency_key}' "
                    "reused with different request body"
                )
                raise ValidationApiError(
                    code="idempotency_key_reused",
                    public_message=msg,
                ) from None

            # Same key, same body - return cached response
            response_status = existing.response_status
            response_body = existing.response_body.encode()
            response_headers_dict = existing.response_headers or {}

            # Add the replay header
            response_headers_dict["X-Idempotent-Replay"] = "true"

            # Send cached response
            headers_list = [
                (
                    k.lower().encode() if isinstance(k, str) else k,
                    v.encode() if isinstance(v, str) else v,
                )
                for k, v in response_headers_dict.items()
            ]

            await send({
                "type": "http.response.start",
                "status": response_status,
                "headers": headers_list,
            })

            if response_body:
                await send({
                    "type": "http.response.body",
                    "body": response_body,
                })

            await send({
                "type": "http.response.body",
                "body": b"",
            })
