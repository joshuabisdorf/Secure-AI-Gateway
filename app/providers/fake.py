from uuid import uuid4

from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChoiceMessage,
)
from app.providers.base import Provider


class FakeProvider(Provider):
    name = "fake"

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """
        RME

        Requires:
            - request is a validated chat-completion request.

        Modifies:
            - Nothing.

        Effects:
            - Produces a deterministic fake model response for testing.

        Inputs:
            - request: The requested model and messages.

        Outputs:
            - A synthetic OpenAI-style chat-completion response.
        """
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid4()}",
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChoiceMessage(
                        role="assistant",
                        content="Fake provider response.",
                    ),
                    finish_reason="stop",
                )
            ],
        )
