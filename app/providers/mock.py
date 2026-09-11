from uuid import uuid4

from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatUsage,
    ChoiceMessage,
    NamedToolChoice,
    ToolCall,
    ToolCallFunction,
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
            - Emits an empty-object function call when a named tool_choice is requested.
            - Includes deterministic token and zero-cost usage accounting.

        Inputs:
            - request: The requested model, messages, and optional named tool choice.

        Outputs:
            - A synthetic OpenAI-style chat-completion response.
        """
        message = ChoiceMessage(
            role="assistant",
            content="Mock provider response.",
        )
        finish_reason = "stop"
        if isinstance(request.tool_choice, NamedToolChoice):
            message = ChoiceMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"call_mock_{uuid4().hex}",
                        function=ToolCallFunction(
                            name=request.tool_choice.function.name,
                            arguments="{}",
                        ),
                    )
                ],
            )
            finish_reason = "tool_calls"

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid4()}",
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=message,
                    finish_reason=finish_reason,
                )
            ],
            usage=ChatUsage(
                prompt_tokens=2,
                completion_tokens=3,
                total_tokens=5,
                cost=0.0,
            ),
        )
