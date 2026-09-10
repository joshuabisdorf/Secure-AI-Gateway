import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app import main
from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatUsage,
    ChoiceMessage,
    NamedToolChoice,
    NamedToolChoiceFunction,
)
from app.providers.base import Provider
from app.providers.openai import OpenAIProvider
from app.tool_authorization import (
    authorize_request_tools,
    parse_client_allowed_tools,
)


class CapturingProvider(Provider):
    name = "capturing"

    def __init__(self) -> None:
        self.request: ChatCompletionRequest | None = None

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        self.request = request
        return ChatCompletionResponse(
            id="chatcmpl-tool-auth-test",
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChoiceMessage(role="assistant", content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=ChatUsage(
                prompt_tokens=2,
                completion_tokens=1,
                total_tokens=3,
                cost=0.0,
            ),
        )


def _tool(name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Test function {name}",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }


def _configure_chat_policy(monkeypatch) -> None:
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")
    monkeypatch.setenv("SAG_CLIENT_PII_POLICIES", "test-client:redact")
    monkeypatch.setenv("SAG_CLIENT_PROMPT_INJECTION_POLICIES", "test-client:audit")


def test_parse_client_allowed_tools_supports_explicit_none_and_multiple_grants() -> None:
    """
    RME

    Requires:
        - Tool policy records use client_id:tool_name format.

    Modifies:
        - Nothing.

    Effects:
        - Verifies multiple least-privilege grants and explicit no-tool clients.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether parsing is correct.
    """
    allowed = parse_client_allowed_tools(
        "client-a:search,client-a:calculator,client-b:-"
    )

    assert allowed["client-a"] == frozenset({"search", "calculator"})
    assert allowed["client-b"] == frozenset()


def test_named_tool_choice_must_be_declared_in_request() -> None:
    """
    RME

    Requires:
        - A named tool choice may be supplied with a validated chat request.

    Modifies:
        - Nothing.

    Effects:
        - Verifies tool_choice cannot select an undeclared function even if policy allows it.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether undeclared forced tools are rejected.
    """
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": "Hello"}],
        tools=[_tool("search")],
        tool_choice=NamedToolChoice(
            function=NamedToolChoiceFunction(name="calculator")
        ),
    )

    decision = authorize_request_tools(
        request,
        frozenset({"search", "calculator"}),
    )

    assert decision.allowed is False
    assert decision.reason == "tool_choice_not_declared"
    assert decision.denied_tools == ("calculator",)


def test_chat_denies_unauthorized_tool_before_provider(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway client is authenticated but has an explicit no-tool policy.

    Modifies:
        - Temporarily replaces the provider with a capturing provider.

    Effects:
        - Verifies unauthorized tool exposure returns 403 before provider execution.

    Inputs:
        - monkeypatch: pytest fixture used to configure policy/provider state.
        - gateway_api_key: Raw test gateway key for the configured client.

    Outputs:
        - None. Assertions determine whether least privilege is enforced.
    """
    _configure_chat_policy(monkeypatch)
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:-")
    provider = CapturingProvider()
    monkeypatch.setattr(main, "provider", provider)

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Use the calculator."}],
            "tools": [_tool("calculator")],
            "tool_choice": "auto",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Requested tool is not allowed."}
    assert response.headers["X-Tool-Authorization-Action"] == "denied"
    assert response.headers["X-Tool-Requested-Count"] == "1"
    assert provider.request is None


def test_chat_forwards_only_explicitly_allowed_tool(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway client has an explicit calculator function grant.

    Modifies:
        - Temporarily replaces the provider with a capturing provider.

    Effects:
        - Verifies an authorized tool definition and named choice reach the provider.

    Inputs:
        - monkeypatch: pytest fixture used to configure policy/provider state.
        - gateway_api_key: Raw test gateway key for the configured client.

    Outputs:
        - None. Assertions determine whether allowed exposure is preserved.
    """
    _configure_chat_policy(monkeypatch)
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:calculator")
    provider = CapturingProvider()
    monkeypatch.setattr(main, "provider", provider)

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Calculate something."}],
            "tools": [_tool("calculator")],
            "tool_choice": {
                "type": "function",
                "function": {"name": "calculator"},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Tool-Authorization-Action"] == "allowed"
    assert response.headers["X-Tool-Requested-Count"] == "1"
    assert provider.request is not None
    assert provider.request.tools is not None
    assert provider.request.tools[0].function.name == "calculator"
    assert isinstance(provider.request.tool_choice, NamedToolChoice)
    assert provider.request.tool_choice.function.name == "calculator"


def test_chat_fails_closed_without_tool_policy(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway authentication is configured.

    Modifies:
        - Temporarily removes SAG_CLIENT_ALLOWED_TOOLS.

    Effects:
        - Verifies valid protected chat requests fail closed without tool policy.

    Inputs:
        - monkeypatch: pytest fixture used to remove policy configuration.
        - gateway_api_key: Raw test gateway key for the configured client.

    Outputs:
        - None. Assertions determine whether missing policy returns 503.
    """
    monkeypatch.delenv("SAG_CLIENT_ALLOWED_TOOLS", raising=False)
    client = TestClient(main.app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Tool authorization policy is not configured."}


def test_openai_provider_forwards_tools_and_normalizes_tool_calls() -> None:
    """
    RME

    Requires:
        - A deterministic HTTP transport can stand in for an OpenAI-compatible provider.

    Modifies:
        - Captured in-memory request payload only.

    Effects:
        - Verifies authorized tools/tool_choice are serialized upstream.
        - Verifies an assistant function tool call is accepted by the gateway response model.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether tool-call proxying works.
    """
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-tool-call",
                "object": "chat.completion",
                "model": "tool-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "calculator",
                                        "arguments": "{\"value\":\"2+2\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    provider = OpenAIProvider(
        api_key="test-provider-key",
        base_url="https://provider.test/v1",
        transport=httpx.MockTransport(handler),
    )
    request = ChatCompletionRequest(
        model="tool-model",
        messages=[{"role": "user", "content": "Calculate 2+2"}],
        tools=[_tool("calculator")],
        tool_choice=NamedToolChoice(
            function=NamedToolChoiceFunction(name="calculator")
        ),
    )

    response = asyncio.run(provider.chat_completion(request))

    assert captured["tools"][0]["function"]["name"] == "calculator"
    assert captured["tool_choice"]["function"]["name"] == "calculator"
    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None
    assert tool_calls[0].function.name == "calculator"
    assert tool_calls[0].function.arguments == '{"value":"2+2"}'
