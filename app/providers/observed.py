from time import perf_counter

from opentelemetry.trace import Status, StatusCode

from app.models import ChatCompletionRequest, ChatCompletionResponse
from app.observability import observe_provider_request, provider_trace
from app.providers.base import Provider


class ObservedProvider(Provider):
    """Provider decorator that adds bounded metrics and tracing."""

    def __init__(self, delegate: Provider) -> None:
        """
        RME

        Requires:
            - delegate is a configured provider implementation.

        Modifies:
            - Nothing outside this wrapper instance.

        Effects:
            - Preserves the provider name while retaining the delegate for calls.

        Inputs:
            - delegate: Provider implementation to instrument.

        Outputs:
            - Initialized ObservedProvider.
        """
        self._delegate = delegate
        self.name = delegate.name

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """
        RME

        Requires:
            - request is a validated chat-completion request.

        Modifies:
            - Provider-specific state in the delegate.
            - Process-local provider metrics and trace state.

        Effects:
            - Records provider latency and success/error counts.
            - Creates a child client span without recording prompts, responses, or credentials.
            - Preserves delegate exceptions unchanged for existing gateway error handling.

        Inputs:
            - request: Normalized gateway chat request.

        Outputs:
            - Delegate provider response.
        """
        started_at = perf_counter()
        with provider_trace(self.name) as span:
            try:
                response = await self._delegate.chat_completion(request)
            except Exception as exc:
                observe_provider_request(
                    self.name,
                    "error",
                    perf_counter() - started_at,
                )
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise

            observe_provider_request(
                self.name,
                "success",
                perf_counter() - started_at,
            )
            return response
