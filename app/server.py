import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.main import app as gateway_app

DEFAULT_MAX_REQUEST_BODY_BYTES = 1_048_576
MIN_MAX_REQUEST_BODY_BYTES = 1_024
MAX_MAX_REQUEST_BODY_BYTES = 16_777_216
_BLOCKED_DOC_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
_SECURITY_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
)


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


def get_api_docs_enabled() -> bool:
    """
    RME

    Requires:
        - SAG_ENABLE_API_DOCS, when set, is a supported boolean value.

    Modifies:
        - Nothing.

    Effects:
        - Fails application startup for ambiguous boolean configuration.

    Inputs:
        - None.

    Outputs:
        - Whether production HTTP routing should expose interactive/OpenAPI docs.
    """
    value = os.getenv("SAG_ENABLE_API_DOCS", "false").strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError("invalid_SAG_ENABLE_API_DOCS")


class RequestBodyLimitMiddleware:
    """Production ASGI boundary for request-size, docs, and response-header hardening."""

    def __init__(
        self,
        app: Any,
        *,
        max_body_bytes: int,
        block_api_docs: bool = False,
    ) -> None:
        """
        RME

        Requires:
            - app is an ASGI application.
            - max_body_bytes is positive.

        Modifies:
            - Middleware instance state.

        Effects:
            - Configures an upper bound for HTTP request bodies.
            - Optionally blocks interactive/OpenAPI documentation routes.

        Inputs:
            - app: Wrapped ASGI application.
            - max_body_bytes: Maximum accepted body size in bytes.
            - block_api_docs: Whether production documentation routes are hidden.

        Outputs:
            - Configured middleware instance.
        """
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes_must_be_positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.block_api_docs = block_api_docs

    async def _send_json_error(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        status: int,
        detail: str,
    ) -> None:
        """
        RME

        Requires:
            - send is the ASGI send callable for an HTTP request.

        Modifies:
            - HTTP response stream.

        Effects:
            - Emits a bounded JSON error without echoing request content.

        Inputs:
            - send: ASGI response sender.
            - status: HTTP error status.
            - detail: Fixed safe error detail.

        Outputs:
            - None.
        """
        body = ('{"detail":"' + detail + '"}').encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            *_SECURITY_HEADERS,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
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
            - HTTP request receive stream and response headers.

        Effects:
            - Rejects declared or streamed request bodies above max_body_bytes with HTTP 413.
            - Buffers only a bounded request body before entering FastAPI, preventing parser amplification.
            - Optionally hides interactive/OpenAPI documentation routes with HTTP 404.
            - Adds cache, framing, MIME-sniffing, referrer, and CSP response protections.
            - Passes non-HTTP traffic through unchanged.

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

        if self.block_api_docs and scope.get("path") in _BLOCKED_DOC_PATHS:
            await self._send_json_error(send, status=404, detail="Not Found")
            return

        content_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if content_lengths:
            if len(set(content_lengths)) != 1:
                await self._send_json_error(
                    send,
                    status=413,
                    detail="Request body too large.",
                )
                return
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                await self._send_json_error(
                    send,
                    status=413,
                    detail="Request body too large.",
                )
                return
            if declared_length < 0 or declared_length > self.max_body_bytes:
                await self._send_json_error(
                    send,
                    status=413,
                    detail="Request body too large.",
                )
                return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                disconnected = True
                break
            if message_type != "http.request":
                continue

            chunk = message.get("body", b"")
            body.extend(chunk)
            if len(body) > self.max_body_bytes:
                await self._send_json_error(
                    send,
                    status=413,
                    detail="Request body too large.",
                )
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if disconnected:
                return {"type": "http.disconnect"}
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        async def hardened_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                existing = {
                    name.lower()
                    for name, _ in message.get("headers", [])
                }
                headers = list(message.get("headers", []))
                headers.extend(
                    (name, value)
                    for name, value in _SECURITY_HEADERS
                    if name not in existing
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, replay_receive, hardened_send)


app = RequestBodyLimitMiddleware(
    gateway_app,
    max_body_bytes=get_max_request_body_bytes(),
    block_api_docs=not get_api_docs_enabled(),
)
