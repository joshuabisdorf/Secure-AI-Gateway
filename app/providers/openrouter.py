import os

import httpx

from app.providers.base import ProviderConfigurationError
from app.providers.openai import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        RME

        Requires:
            - api_key or OPENROUTER_API_KEY provides an OpenRouter API key.
            - base_url, when provided, identifies an OpenRouter-compatible API root.

        Modifies:
            - Provider instance configuration.

        Effects:
            - Configures the shared OpenAI-compatible chat transport for OpenRouter.
            - Requests OpenRouter token and cost usage accounting.
            - Fails closed when no OpenRouter API key is configured.

        Inputs:
            - api_key: Optional explicit OpenRouter API key.
            - base_url: Optional OpenRouter API root override.
            - timeout_seconds: Maximum duration of an upstream request.
            - transport: Optional httpx transport used for deterministic testing.

        Outputs:
            - A configured OpenRouter provider instance.
        """
        resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not resolved_api_key:
            raise ProviderConfigurationError("missing_openrouter_api_key")

        resolved_base_url = (
            base_url
            or os.getenv("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1"
        )

        super().__init__(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
            include_usage=True,
        )
