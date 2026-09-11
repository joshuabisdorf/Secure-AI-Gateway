import os

import httpx
from pydantic import ValidationError

from app.models import ChatCompletionRequest, ChatCompletionResponse, ChatMessage
from app.providers.base import Provider, ProviderConfigurationError, ProviderError


def _serialize_message(message: ChatMessage) -> dict[str, object]:
    """
    RME

    Requires:
        - message is a validated gateway chat message.

    Modifies:
        - Nothing.

    Effects:
        - Serializes OpenAI-compatible message fields for upstream transport.
        - Removes gateway-only execution authorization tickets/risk labels from tool-call history.

    Inputs:
        - message: Gateway chat message.

    Outputs:
        - Provider-safe message payload.
    """
    payload = message.model_dump(exclude_none=True)
    tool_calls = payload.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                tool_call.pop("execution_token", None)
                tool_call.pop("execution_risk", None)
    return payload


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        include_usage: bool = False,
    ) -> None:
        """
        RME

        Requires:
            - api_key or OPENAI_API_KEY provides an upstream OpenAI API key.
            - base_url, when provided, identifies an OpenAI-compatible API root.

        Modifies:
            - Provider instance configuration.

        Effects:
            - Fails closed when no upstream API key is configured.
            - Optionally requests extended usage accounting from compatible providers.

        Inputs:
            - api_key: Optional explicit upstream OpenAI API key.
            - base_url: Optional upstream API root.
            - timeout_seconds: Maximum duration of an upstream request.
            - transport: Optional httpx transport used for deterministic testing.
            - include_usage: Whether to request provider-specific extended usage data.

        Outputs:
            - A configured OpenAI provider instance.
        """
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise ProviderConfigurationError("missing_openai_api_key")

        self._api_key = resolved_api_key
        self._base_url = (
            base_url
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._include_usage = include_usage

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """
        RME

        Requires:
            - request is a validated gateway chat-completion request.
            - Any function tools in request were authorized by the gateway before this call.
            - The provider has a configured OpenAI API key.

        Modifies:
            - Upstream OpenAI-compatible API usage and billing.

        Effects:
            - Sends messages and optional authorized function tools/tool choice upstream.
            - Strips gateway-only execution authorization metadata before transport.
            - Converts the upstream response, including function tool calls, into gateway models.
            - Converts upstream failures into non-secret ProviderError reasons.

        Inputs:
            - request: Requested upstream model, messages, and optional authorized tools.

        Outputs:
            - A normalized chat-completion response.
        """
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [_serialize_message(message) for message in request.messages],
            "stream": False,
        }
        if request.tools is not None:
            payload["tools"] = [
                tool.model_dump(exclude_none=True) for tool in request.tools
            ]
        if request.tool_choice is not None:
            payload["tool_choice"] = (
                request.tool_choice
                if isinstance(request.tool_choice, str)
                else request.tool_choice.model_dump(exclude_none=True)
            )
        if self._include_usage:
            payload["usage"] = {"include": True}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError("upstream_timeout") from exc
        except httpx.RequestError as exc:
            raise ProviderError("upstream_network_error") from exc

        if not response.is_success:
            raise ProviderError(f"upstream_http_{response.status_code}")

        try:
            return ChatCompletionResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderError("invalid_upstream_response") from exc
