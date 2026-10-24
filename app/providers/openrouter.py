"""Implement an OpenRouter adapter using the shared transport."""

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
        """Configure OpenRouter with the shared OpenAI-compatible transport.

        :param api_key: Optional explicit OpenRouter API key.
        :param base_url: Optional OpenRouter API root override.
        :param timeout_seconds: Maximum duration of an upstream request.
        :param transport: Optional httpx transport used for deterministic
            testing.

        .. note::

            - Precondition: api_key or OPENROUTER_API_KEY provides an
              OpenRouter API key.
            - Precondition: base_url, when provided, identifies an
              OpenRouter-compatible API root.
            - Side effect: Provider instance configuration.
            - OpenRouter.
            - Requests OpenRouter token and cost usage accounting.
            - Fails closed when no OpenRouter API key is configured.
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
