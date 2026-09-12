import os

from app.providers.base import Provider, ProviderConfigurationError
from app.providers.mock import MockProvider
from app.providers.observed import ObservedProvider
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
        - Wraps the selected provider with bounded metrics and trace instrumentation.
        - Fails closed for unsupported provider names or invalid provider configuration.

    Inputs:
        - None.

    Outputs:
        - An instrumented configured provider implementation.
    """
    provider_name = os.getenv("SAG_PROVIDER", "mock").strip().lower()

    if provider_name in {"mock", "fake"}:
        provider: Provider = MockProvider()
    elif provider_name == "openai":
        provider = OpenAIProvider()
    elif provider_name == "openrouter":
        provider = OpenRouterProvider()
    else:
        raise ProviderConfigurationError("unsupported_provider")

    return ObservedProvider(provider)
