import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.main import app as gateway_app

DEFAULT_MAX_REQUEST_BODY_BYTES = 1_048_576
MIN_MAX_REQUEST_BODY_BYTES = 1_024
MAX_MAX_REQUEST_BODY_BYTES = 16_777_216


class RequestBodyTooLarge(Exception):
    """Raised internally when a streamed HTTP body exceeds the configured limit."""


def get_max_request_body_bytes() -> int:
    """
    RME

    Requires:
        - SAG_MAX_REQUEST_BODY_BYTES, when set, contains an integer byte count.

    Modifies:
        - Nothing.

    Effects:
        - Fails application startup when the configured limit is invalid or outside the safe range.

    Inputs:
        - None.

    Outputs:
        - Maximum accepted HTTP request-body size in bytes.
    """
    raw_value = os.getenv(
        "SAG_MAX_REQUEST_BODY_BYTES",
        str(DEFAULT_MAX_REQUEST_BODY_BYTES),
    )
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("invalid_SAG_MAX_REQUEST_BODY_BYTES") from exc

    if not MIN_MAX_REQUEST_BODY_BYTES <= value <= MAX_MAX_REQUEST_BODY_BYTES:
        raise ValueError("SAG_MAX_REQUEST_BODY_BYTES_out_of_range")
    return value


class RequestBodyLimitMiddleware:
    """Pure ASGI request-body limiter that rejects oversized bodies before routing."""

    def __init__(self, app: Any, *, max_body_bytes: int) -> None:
        """
        RME

        Requires:
            - app is an ASGI application.
            - max_body_bytes is positive.

        Modifies:
            - Middleware instance state.

        Effects:
            - Configures an upper bound for HTTP request bodies.

        Inputs:
            - app: Wrapped ASGI application.
            - max_body_bytes: Maximum accepted body size in bytes.

        Outputs:
            - Configured middleware instance.
        """
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes_must_be_positive")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def _reject(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """
        RME

        Requires:
            - send is the ASGI send callable for an HTTP request.

        Modifies:
            - HTTP response stream.

        Effects:
            - Emits a bounded JSON 413 response without echoing request content.

        Inputs:
            - send: ASGI response sender.

        Outputs:
            - None.
        """
        body = b'{"detail":"Request body too large."}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """
        RME

        Requires:
            - scope, receive, and send follow the ASGI protocol.

        Modifies:
            - HTTP receive stream when the request has a body.

        Effects:
            - Rejects declared or streamed request bodies above max_body_bytes with HTTP 413.
            - Passes non-HTTP traffic and in-bound HTTP requests through unchanged.

        Inputs:
            - scope: ASGI connection scope.
            - receive: ASGI request receiver.
            - send: ASGI response sender.

        Outputs:
            - None.
        """
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if content_lengths:
            if len(set(content_lengths)) != 1:
                await self._reject(send)
                return
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                await self._reject(send)
                return
            if declared_length < 0 or declared_length > self.max_body_bytes:
                await self._reject(send)
                return

        received_bytes = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                received_bytes += len(body)
                if received_bytes > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(send)


app = RequestBodyLimitMiddleware(
    gateway_app,
    max_body_bytes=get_max_request_body_bytes(),
)
