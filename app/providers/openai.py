import os

import httpx
from pydantic import ValidationError

from app.models import ChatCompletionRequest, ChatCompletionResponse
from app.providers.base import Provider, ProviderConfigurationError, ProviderError


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
            - api_key: Optional explicit upstream API key.
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
            - The provider has a configured OpenAI API key.

        Modifies:
            - Upstream OpenAI API usage and billing.

        Effects:
            - Sends the normalized request to the OpenAI Chat Completions API.
            - Converts the upstream response into the gateway response model.
            - Converts upstream failures into non-secret ProviderError reasons.

        Inputs:
            - request: Requested upstream model and chat messages.

        Outputs:
            - A normalized chat-completion response.
        """
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": False,
        }
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
