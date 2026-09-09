from uuid import uuid4

from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatUsage,
    ChoiceMessage,
)
from app.providers.base import Provider


class MockProvider(Provider):
    name = "mock"

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
            - Produces a deterministic non-network model response for testing.
            - Includes deterministic token and zero-cost usage accounting.

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
                        content="Mock provider response.",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=ChatUsage(
                prompt_tokens=2,
                completion_tokens=3,
                total_tokens=5,
                cost=0.0,
            ),
        )
