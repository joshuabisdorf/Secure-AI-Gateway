import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.audit import emit_audit_event
from app.auth import authenticate_api_key
from app.models import ChatCompletionRequest, ChatCompletionResponse
from app.policies.model_access import enforce_model_allowed
from app.providers.base import ProviderError
from app.providers.factory import build_provider

app = FastAPI(
    title="Secure AI Gateway",
    version="0.1.0",
)

provider = build_provider()
_request_id_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def resolve_request_id(candidate: str | None) -> str:
    """
    RME

    Requires:
        - candidate may contain a caller-supplied request identifier.

    Modifies:
        - Nothing.

    Effects:
        - Generates a new request identifier when the supplied value is absent or unsafe.

    Inputs:
        - candidate: Optional X-Request-ID header value.

    Outputs:
        - A validated caller request ID or a newly generated gateway request ID.
    """
    if candidate is not None and _request_id_pattern.fullmatch(candidate):
        return candidate

    return f"req_{uuid4().hex}"


@app.middleware("http")
async def add_request_context(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """
    RME

    Requires:
        - request is an incoming HTTP request.
        - call_next invokes the remaining FastAPI request pipeline.

    Modifies:
        - request.state with request_id and started_at values.
        - The outgoing response headers.

    Effects:
        - Assigns a request correlation ID and starts latency measurement.
        - Adds X-Request-ID to the response.

    Inputs:
        - request: Incoming HTTP request.
        - call_next: Callable for the remaining request pipeline.

    Outputs:
        - The HTTP response with an X-Request-ID header.
    """
    request_id = resolve_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    request.state.started_at = perf_counter()

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """
    RME

    Requires:
        - The FastAPI application is running.

    Modifies:
        - Nothing.

    Effects:
        - Reports the health status of the gateway.

    Inputs:
        - None.

    Outputs:
        - A dictionary containing the gateway health status.
    """
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completion(
    request: ChatCompletionRequest,
    http_request: Request,
    _: str = Depends(authenticate_api_key),
) -> ChatCompletionResponse:
    """
    RME

    Requires:
        - request satisfies the gateway chat-completion schema.
        - The caller provides a valid gateway API key.
        - The requested model is explicitly allowed by gateway policy.

    Modifies:
        - Provider-specific state, if any.
        - The audit logging stream.

    Effects:
        - Authenticates the caller.
        - Enforces and records the configured model allowlist decision.
        - Sends the normalized request to the configured provider when allowed.
        - Records requested and resolved model identities on successful completion.
        - Records completion outcome and request latency without prompt content.
        - Converts known upstream provider failures to a generic 502 response.

    Inputs:
        - request: Requested model and chat messages.
        - http_request: HTTP request containing request ID and timing context.
        - _: Authenticated gateway credential supplied by dependency injection.

    Outputs:
        - An OpenAI-style chat-completion response.
    """
    request_id = http_request.state.request_id

    try:
        enforce_model_allowed(request.model)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="model_policy",
            outcome="deny",
            requested_model=request.model,
            reason=str(exc.detail),
        )
        raise

    emit_audit_event(
        request_id=request_id,
        event="model_policy",
        outcome="allow",
        requested_model=request.model,
    )

    try:
        response = await provider.chat_completion(request)
    except ProviderError as exc:
        latency_ms = (perf_counter() - http_request.state.started_at) * 1000
        emit_audit_event(
            request_id=request_id,
            event="chat_completion",
            outcome="error",
            requested_model=request.model,
            provider=provider.name,
            reason=exc.reason,
            latency_ms=latency_ms,
        )
        raise HTTPException(
            status_code=502,
            detail="Upstream provider request failed.",
        ) from exc
    except Exception as exc:
        latency_ms = (perf_counter() - http_request.state.started_at) * 1000
        emit_audit_event(
            request_id=request_id,
            event="chat_completion",
            outcome="error",
            requested_model=request.model,
            provider=provider.name,
            reason=type(exc).__name__,
            latency_ms=latency_ms,
        )
        raise

    latency_ms = (perf_counter() - http_request.state.started_at) * 1000
    emit_audit_event(
        request_id=request_id,
        event="chat_completion",
        outcome="success",
        requested_model=request.model,
        resolved_model=response.model,
        provider=provider.name,
        latency_ms=latency_ms,
    )
    return response
