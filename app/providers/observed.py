from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from typing import Any, TypeVar

from opentelemetry.trace import Status, StatusCode

from app.observability import observe_provider_request, provider_trace

_Result = TypeVar("_Result")


def observe_provider_chat(
    method: Callable[[Any, Any], Awaitable[_Result]],
) -> Callable[[Any, Any], Awaitable[_Result]]:
    """Preserve the concrete provider object type and original method metadata.

    :param method: Async provider chat-completion method to instrument.
    :returns: Instrumented async provider method with the same call contract.

    .. note::

        - Precondition: method is an async provider instance method.
        - Precondition: The provider instance exposes a non-secret name
          attribute.
        - Side effect: Process-local provider metrics and trace state when the
          wrapped method runs.
        - Records provider request latency and success/error counts.
        - Creates a child client span without recording prompts, responses, or
          credentials.
        - Preserves provider return values and exceptions unchanged.
    """

    @wraps(method)
    async def wrapped(self: Any, request: Any) -> _Result:
        """Measure one exact provider call.

        :param self: Concrete provider instance.
        :param request: Provider request passed through unchanged.
        :returns: Exact result returned by the decorated method.

        .. note::

            - Precondition: self is a provider instance accepted by the
              decorated method.
            - Precondition: request is a value accepted by the decorated
              method.
            - Side effect: Provider-specific state through the decorated
              method.
            - Side effect: Process-local provider metrics and trace state.
            - Records exception metadata on the trace without changing the
              exception.
        """
        provider_name = getattr(self, "name", "unknown")
        started_at = perf_counter()
        with provider_trace(provider_name) as span:
            try:
                response = await method(self, request)
            except Exception as exc:
                observe_provider_request(
                    provider_name,
                    "error",
                    perf_counter() - started_at,
                )
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise

            observe_provider_request(
                provider_name,
                "success",
                perf_counter() - started_at,
            )
            return response

    return wrapped
