import asyncio

import pytest
from pydantic import ValidationError

from app.models import (
    MAX_MESSAGE_CONTENT_LENGTH,
    MAX_MESSAGES,
    MAX_REQUEST_TEXT_CHARS,
    ChatCompletionRequest,
    ToolFunction,
)
from app.server import RequestBodyLimitMiddleware


def test_chat_request_rejects_too_many_messages() -> None:
    payload = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": "x"}] * (MAX_MESSAGES + 1),
    }

    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(payload)


def test_chat_request_rejects_oversized_single_message() -> None:
    payload = {
        "model": "openrouter/free",
        "messages": [
            {
                "role": "user",
                "content": "x" * (MAX_MESSAGE_CONTENT_LENGTH + 1),
            }
        ],
    }

    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(payload)


def test_chat_request_rejects_aggregate_text_amplification() -> None:
    per_message = MAX_REQUEST_TEXT_CHARS // 3
    payload = {
        "model": "openrouter/free",
        "messages": [
            {"role": "user", "content": "x" * per_message},
            {"role": "user", "content": "y" * per_message},
            {"role": "user", "content": "z" * per_message},
            {"role": "user", "content": "overflow"},
        ],
    }

    with pytest.raises(ValidationError, match="aggregate text size"):
        ChatCompletionRequest.model_validate(payload)


def test_chat_request_rejects_unsupported_top_level_fields() -> None:
    payload = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.2,
    }

    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(payload)


def test_chat_request_rejects_streaming() -> None:
    payload = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(payload)


def test_tool_schema_rejects_excessive_nesting() -> None:
    nested: object = {"type": "string"}
    for _ in range(20):
        nested = {"properties": {"next": nested}}

    with pytest.raises(ValidationError, match="nesting depth"):
        ToolFunction(name="deep_tool", parameters=nested)


def test_request_body_limit_rejects_declared_oversize() -> None:
    inner_called = False
    sent: list[dict[str, object]] = []

    async def inner(scope, receive, send) -> None:
        nonlocal inner_called
        inner_called = True

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(inner, max_body_bytes=8)
    scope = {
        "type": "http",
        "headers": [(b"content-length", b"9")],
    }

    asyncio.run(middleware(scope, receive, send))

    assert inner_called is False
    assert sent[0]["status"] == 413
    assert b"Request body too large" in sent[1]["body"]


def test_request_body_limit_rejects_streamed_oversize() -> None:
    sent: list[dict[str, object]] = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": False},
        ]
    )

    async def inner(scope, receive, send) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, object]:
        return next(chunks)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(inner, max_body_bytes=8)
    scope = {"type": "http", "headers": []}

    asyncio.run(middleware(scope, receive, send))

    assert sent[0]["status"] == 413
    assert b"Request body too large" in sent[1]["body"]


def test_request_body_limit_allows_bounded_stream_and_adds_security_headers() -> None:
    sent: list[dict[str, object]] = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
    )

    async def inner(scope, receive, send) -> None:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        assert bytes(body) == b"12345678"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, object]:
        return next(chunks)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(inner, max_body_bytes=8)
    scope = {"type": "http", "headers": []}

    asyncio.run(middleware(scope, receive, send))

    assert sent[0]["status"] == 204
    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"


def test_production_boundary_blocks_api_docs() -> None:
    inner_called = False
    sent: list[dict[str, object]] = []

    async def inner(scope, receive, send) -> None:
        nonlocal inner_called
        inner_called = True

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(
        inner,
        max_body_bytes=1_024,
        block_api_docs=True,
    )
    scope = {"type": "http", "path": "/openapi.json", "headers": []}

    asyncio.run(middleware(scope, receive, send))

    assert inner_called is False
    assert sent[0]["status"] == 404
