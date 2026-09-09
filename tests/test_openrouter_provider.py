import asyncio
import json

import httpx
import pytest

from app.models import ChatCompletionRequest
from app.providers.base import ProviderConfigurationError
from app.providers.factory import build_provider
from app.providers.openrouter import OpenRouterProvider


def test_openrouter_provider_forwards_request() -> None:
    """
    RME

    Requires:
        - The OpenRouter provider can use an injected mock HTTP transport.

    Modifies:
        - Nothing outside the temporary mock HTTP client.

    Effects:
        - Verifies OpenRouter requests use the expected endpoint and bearer key.
        - Verifies an OpenAI-compatible response is normalized by the gateway.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether forwarding works correctly.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openrouter.example/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-openrouter-key"

        payload = json.loads(request.content.decode())
        assert payload == {
            "model": "openrouter/free",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }

        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-openrouter-test",
                "object": "chat.completion",
                "model": "openrouter/free",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello from OpenRouter",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenRouterProvider(
        api_key="test-openrouter-key",
        base_url="https://openrouter.example/api/v1",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.chat_completion(
            ChatCompletionRequest(
                model="openrouter/free",
                messages=[{"role": "user", "content": "Hello"}],
            )
        )
    )

    assert result.id == "chatcmpl-openrouter-test"
    assert result.choices[0].message.content == "Hello from OpenRouter"


def test_openrouter_provider_fails_closed_without_api_key(monkeypatch) -> None:
    """
    RME

    Requires:
        - OPENROUTER_API_KEY may be absent from the test environment.

    Modifies:
        - Temporarily removes OPENROUTER_API_KEY from the test environment.

    Effects:
        - Verifies the provider refuses to initialize without credentials.

    Inputs:
        - monkeypatch: pytest fixture used to modify the environment.

    Outputs:
        - None. Assertions determine whether configuration fails closed.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError) as exc_info:
        OpenRouterProvider()

    assert exc_info.value.reason == "missing_openrouter_api_key"


def test_factory_selects_openrouter_provider(monkeypatch) -> None:
    """
    RME

    Requires:
        - SAG_PROVIDER and OPENROUTER_API_KEY may be configured for the test.

    Modifies:
        - Temporarily configures provider environment variables.

    Effects:
        - Verifies the provider factory selects OpenRouter explicitly.

    Inputs:
        - monkeypatch: pytest fixture used to modify the environment.

    Outputs:
        - None. Assertions determine whether provider selection works correctly.
    """
    monkeypatch.setenv("SAG_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    provider = build_provider()

    assert isinstance(provider, OpenRouterProvider)
    assert provider.name == "openrouter"
