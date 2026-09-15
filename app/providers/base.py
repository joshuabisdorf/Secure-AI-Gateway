from abc import ABC, abstractmethod

from app.models import ChatCompletionRequest, ChatCompletionResponse


class ProviderError(RuntimeError):
    """A safe, non-secret upstream provider failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ProviderConfigurationError(ProviderError):
    """A provider configuration error detected before forwarding a request."""


class Provider(ABC):
    name: str

    @abstractmethod
    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """
        RME

        Requires:
            - request is a validated chat-completion request.

        Modifies:
            - Provider-specific state, if any.

        Effects:
            - Executes a chat-completion request through the provider.

        Inputs:
            - request: The normalized gateway chat request.

        Outputs:
            - A normalized chat-completion response.
        """
        raise NotImplementedError
