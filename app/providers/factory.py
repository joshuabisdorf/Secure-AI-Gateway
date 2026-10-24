"""Select the configured LLM provider implementation."""

import os

from app.providers.base import Provider, ProviderConfigurationError
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.openrouter import OpenRouterProvider


def build_provider() -> Provider:
    """Select the gateway upstream provider.

    :returns: A configured concrete provider implementation.

    .. note::

        - Precondition: SAG_PROVIDER may identify a supported upstream
          provider.
        - Precondition: Provider-specific environment variables are configured
          when required.
        - Side effect: Nothing outside the returned provider instance.
        - Returns the concrete provider type; provider methods own
          observability instrumentation.
        - Fails closed for unsupported provider names or invalid provider
          configuration.
    """
    provider_name = os.getenv("SAG_PROVIDER", "mock").strip().lower()

    if provider_name in {"mock", "fake"}:
        return MockProvider()
    if provider_name == "openai":
        return OpenAIProvider()
    if provider_name == "openrouter":
        return OpenRouterProvider()

    raise ProviderConfigurationError("unsupported_provider")
