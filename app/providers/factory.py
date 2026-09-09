import os

from app.providers.base import Provider, ProviderConfigurationError
from app.providers.fake import FakeProvider
from app.providers.openai import OpenAIProvider


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
    provider_name = os.getenv("SAG_PROVIDER", "fake").strip().lower()

    if provider_name == "fake":
        return FakeProvider()
    if provider_name == "openai":
        return OpenAIProvider()

    raise ProviderConfigurationError("unsupported_provider")
