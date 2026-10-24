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
        """Execute a chat-completion request through the provider.

        Args:
            request: The normalized gateway chat request.

        Returns:
            A normalized chat-completion response.

        Notes:
            - Precondition: request is a validated chat-completion request.
            - Side effect: Provider-specific state, if any.
        """
        raise NotImplementedError
