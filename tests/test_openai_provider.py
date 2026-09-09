import asyncio
import json

import httpx
import pytest

from app.models import ChatCompletionRequest
from app.providers.base import ProviderConfigurationError, ProviderError
from app.providers.openai import OpenAIProvider


def test_openai_provider_forwards_and_normalizes_response() -> None:
    """
    RME

    Requires:
        - The OpenAI provider can use an injected mock HTTP transport.

    Modifies:
        - Nothing outside the temporary mock HTTP client.

    Effects:
        - Verifies the provider sends the expected upstream request.
        - Verifies an OpenAI-style response is normalized by the gateway model.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether forwarding works correctly.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-openai-key"

        payload = json.loads(request.content.decode())
        assert payload == {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }

        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hi",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenAIProvider(
        api_key="test-openai-key",
        base_url="https://api.example.test/v1",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.chat_completion(
            ChatCompletionRequest(
                model="test-model",
                messages=[{"role": "user", "content": "Hello"}],
            )
        )
    )

    assert result.id == "chatcmpl-test"
    assert result.choices[0].message.content == "Hi"


def test_openai_provider_fails_closed_without_api_key(monkeypatch) -> None:
    """
    RME

    Requires:
        - OPENAI_API_KEY may be absent from the test environment.

    Modifies:
        - Temporarily removes OPENAI_API_KEY from the test environment.

    Effects:
        - Verifies the provider refuses to initialize without credentials.

    Inputs:
        - monkeypatch: pytest fixture used to modify the environment.

    Outputs:
        - None. Assertions determine whether configuration fails closed.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError) as exc_info:
        OpenAIProvider()

    assert exc_info.value.reason == "missing_openai_api_key"


def test_openai_provider_sanitizes_upstream_error() -> None:
    """
    RME

    Requires:
        - The OpenAI provider can use an injected mock HTTP transport.

    Modifies:
        - Nothing outside the temporary mock HTTP client.

    Effects:
        - Simulates an upstream authentication failure.
        - Verifies provider response details are not exposed in the exception.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether the error is sanitized.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "sensitive upstream detail"}},
        )

    provider = OpenAIProvider(
        api_key="test-openai-key",
        base_url="https://api.example.test/v1",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(
            provider.chat_completion(
                ChatCompletionRequest(
                    model="test-model",
                    messages=[{"role": "user", "content": "Hello"}],
                )
            )
        )

    assert exc_info.value.reason == "upstream_http_401"
    assert "sensitive upstream detail" not in str(exc_info.value)
