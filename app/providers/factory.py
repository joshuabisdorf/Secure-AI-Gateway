import os

from app.providers.base import Provider, ProviderConfigurationError
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.openrouter import OpenRouterProvider


def build_provider() -> Provider:
    """
    RME

    Requires:
        - SAG_PROVIDER may identify a supported upstream provider.
        - Provider-specific environment variables are configured when required.

    Modifies:
        - Nothing outside the returned provider instance.

    Effects:
        - Selects the gateway upstream provider.
        - Fails closed for unsupported provider names or invalid provider configuration.

    Inputs:
        - None.

    Outputs:
        - A configured provider implementation.
    """
    provider_name = os.getenv("SAG_PROVIDER", "mock").strip().lower()

    if provider_name in {"mock", "fake"}:
        return MockProvider()
    if provider_name == "openai":
        return OpenAIProvider()
    if provider_name == "openrouter":
        return OpenRouterProvider()

    raise ProviderConfigurationError("unsupported_provider")
