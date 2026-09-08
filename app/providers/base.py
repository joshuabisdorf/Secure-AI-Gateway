from abc import ABC, abstractmethod

from app.models import ChatCompletionRequest, ChatCompletionResponse


class Provider(ABC):
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