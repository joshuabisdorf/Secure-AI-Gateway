import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.api_keys import Principal
from app.audit import emit_audit_event
from app.auth import authenticate_api_key
from app.models import ChatCompletionRequest, ChatCompletionResponse
from app.policies.model_access import enforce_model_allowed
from app.providers.base import ProviderError
from app.providers.factory import build_provider
from app.rate_limit import InMemoryRateLimiter, get_client_rate_limit

app = FastAPI(
    title="Secure AI Gateway",
    version="0.1.0",
)

provider = build_provider()
rate_limiter = InMemoryRateLimiter()
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
    outgoing_response: Response,
    principal: Principal = Depends(authenticate_api_key),
) -> ChatCompletionResponse:
    """
    RME

    Requires:
        - request satisfies the gateway chat-completion schema.
        - The caller provides a valid gateway API key mapped to a client identity.
        - Rate-limit policy is configured for the authenticated client.
        - The requested model is allowed globally and for the authenticated client.

    Modifies:
        - Process-local per-client rate-limit state.
        - Provider-specific state, if any.
        - The audit logging stream.
        - Rate-limit headers on successful responses.

    Effects:
        - Authenticates and identifies the caller.
        - Applies and records per-client request-rate limits before model/provider work.
        - Returns 429 with Retry-After when the client exceeds its configured rate.
        - Enforces deployment-wide and per-client model allowlists.
        - Records the resulting policy decision with client attribution.
        - Sends the normalized request to the configured provider when allowed.
        - Records requested and resolved model identities on successful completion.
        - Records completion outcome and request latency without prompt content.
        - Converts known upstream provider failures to a generic 502 response.

    Inputs:
        - request: Requested model and chat messages.
        - http_request: HTTP request containing request ID and timing context.
        - outgoing_response: FastAPI response used to expose rate-limit headers.
        - principal: Authenticated client identity supplied by dependency injection.

    Outputs:
        - An OpenAI-style chat-completion response.
    """
    request_id = http_request.state.request_id

    try:
        limit_rpm = get_client_rate_limit(principal.client_id)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="rate_limit",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=str(exc.detail),
        )
        raise

    rate_decision = rate_limiter.check(principal.client_id, limit_rpm)
    if not rate_decision.allowed:
        retry_after_seconds = rate_decision.retry_after_seconds or 1
        emit_audit_event(
            request_id=request_id,
            event="rate_limit",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason="rate_limit_exceeded",
            limit_rpm=rate_decision.limit_rpm,
            remaining=rate_decision.remaining,
            retry_after_seconds=retry_after_seconds,
        )
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded.",
            headers={
                "Retry-After": str(retry_after_seconds),
                "X-RateLimit-Limit": str(rate_decision.limit_rpm),
                "X-RateLimit-Remaining": str(rate_decision.remaining),
            },
        )

    outgoing_response.headers["X-RateLimit-Limit"] = str(rate_decision.limit_rpm)
    outgoing_response.headers["X-RateLimit-Remaining"] = str(rate_decision.remaining)
    emit_audit_event(
        request_id=request_id,
        event="rate_limit",
        outcome="allow",
        client_id=principal.client_id,
        key_id=principal.key_id,
        limit_rpm=rate_decision.limit_rpm,
        remaining=rate_decision.remaining,
    )

    try:
        enforce_model_allowed(request.model, principal.client_id)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="model_policy",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            requested_model=request.model,
            reason=str(exc.detail),
        )
        raise

    emit_audit_event(
        request_id=request_id,
        event="model_policy",
        outcome="allow",
        client_id=principal.client_id,
        key_id=principal.key_id,
        requested_model=request.model,
    )

    try:
        provider_response = await provider.chat_completion(request)
    except ProviderError as exc:
        latency_ms = (perf_counter() - http_request.state.started_at) * 1000
        emit_audit_event(
            request_id=request_id,
            event="chat_completion",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
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
            client_id=principal.client_id,
            key_id=principal.key_id,
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
        client_id=principal.client_id,
        key_id=principal.key_id,
        requested_model=request.model,
        resolved_model=provider_response.model,
        provider=provider.name,
        latency_ms=latency_ms,
    )
    return provider_response
