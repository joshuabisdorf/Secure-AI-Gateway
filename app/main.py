import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.api_keys import Principal
from app.audit import emit_audit_event
from app.auth import authenticate_api_key, client_registry
from app.models import ChatCompletionRequest, ChatCompletionResponse
from app.policies.model_access import enforce_model_allowed
from app.providers.base import ProviderError
from app.providers.factory import build_provider
from app.rate_limit import InMemoryRateLimiter, get_client_rate_limit
from app.usage_budget import (
    UsageBudgetDecision,
    UsageLedgerUnavailable,
    build_usage_ledger,
    get_client_usage_budget,
)

provider = build_provider()
rate_limiter = InMemoryRateLimiter()
usage_ledger = build_usage_ledger()
_request_id_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    RME

    Requires:
        - Configured persistent resources may own async connection pools.

    Modifies:
        - Client-registry and usage-ledger connection-pool state during shutdown.

    Effects:
        - Leaves database connections lazy during startup.
        - Closes opened PostgreSQL pools cleanly on application shutdown.

    Inputs:
        - _: FastAPI application instance.

    Outputs:
        - Async lifespan context for FastAPI.
    """
    yield

    for resource in (client_registry, usage_ledger):
        close = getattr(resource, "close", None)
        if close is not None:
            await close()


app = FastAPI(
    title="Secure AI Gateway",
    version="0.1.0",
    lifespan=lifespan,
)


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


def _budget_audit_fields(
    decision: UsageBudgetDecision,
    *,
    include_cost: bool,
) -> dict[str, int | float | str | None]:
    """
    RME

    Requires:
        - decision contains current daily usage state.
        - include_cost is true only when cost accounting is known or enforced.

    Modifies:
        - Nothing.

    Effects:
        - Converts internal budget state into safe audit fields.
        - Omits cumulative cost fields when provider cost is not known.

    Inputs:
        - decision: Current daily usage-budget decision.
        - include_cost: Whether cost accounting should be exposed.

    Outputs:
        - Keyword arguments suitable for emit_audit_event.
    """
    fields: dict[str, int | float | str | None] = {
        "token_limit_daily": decision.token_limit_daily,
        "tokens_used_daily": decision.tokens_used_daily,
        "tokens_remaining_daily": decision.tokens_remaining_daily,
        "budget_reset_at": decision.reset_at.isoformat(),
    }

    if include_cost:
        fields.update(
            {
                "cost_limit_daily_usd": (
                    float(decision.cost_limit_daily_usd)
                    if decision.cost_limit_daily_usd is not None
                    else None
                ),
                "cost_used_daily_usd": float(decision.cost_used_daily_usd),
                "cost_remaining_daily_usd": (
                    float(decision.cost_remaining_daily_usd)
                    if decision.cost_remaining_daily_usd is not None
                    else None
                ),
            }
        )
    return fields


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
    """Return the gateway process health status."""
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
        - The caller provides a valid database-backed gateway API key.
        - Rate-limit, model, and daily usage-budget policies are configured.
        - Persistent usage accounting is available.

    Modifies:
        - Process-local rate-limit state.
        - Persistent per-client daily usage totals in PostgreSQL.
        - Provider-specific state, if any.
        - The audit logging stream and response policy headers.

    Effects:
        - Applies authentication, rate limiting, and model authorization.
        - Reads durable accumulated usage before provider forwarding.
        - Records provider-reported usage atomically after successful completion.
        - Fails closed when persistent accounting is unavailable.
        - Returns the request that crosses a budget, then blocks later requests.

    Inputs:
        - request: Requested model and chat messages.
        - http_request: HTTP request containing request ID and timing context.
        - outgoing_response: FastAPI response used to expose policy headers.
        - principal: Authenticated client identity supplied by dependency injection.

    Outputs:
        - An OpenAI-style chat-completion response with optional usage data.
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
        usage_budget = get_client_usage_budget(principal.client_id)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="usage_budget",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=str(exc.detail),
        )
        raise

    try:
        budget_decision = await usage_ledger.check(principal.client_id, usage_budget)
    except UsageLedgerUnavailable as exc:
        emit_audit_event(
            request_id=request_id,
            event="usage_budget",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason="usage_ledger_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Usage accounting is unavailable.",
        ) from exc

    if not budget_decision.allowed:
        emit_audit_event(
            request_id=request_id,
            event="usage_budget",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=budget_decision.reason,
            **_budget_audit_fields(
                budget_decision,
                include_cost=usage_budget.cost_limit_daily_usd is not None,
            ),
        )
        raise HTTPException(
            status_code=403,
            detail="Usage budget exceeded.",
            headers={"X-Usage-Budget-Reset": budget_decision.reset_at.isoformat()},
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

    usage = provider_response.usage
    missing_required_cost = (
        usage_budget.cost_limit_daily_usd is not None
        and (usage is None or usage.cost is None)
    )
    if usage is None or missing_required_cost:
        emit_audit_event(
            request_id=request_id,
            event="usage_budget",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            provider=provider.name,
            reason="missing_usage_data",
        )
        raise HTTPException(
            status_code=502,
            detail="Upstream provider usage data is unavailable.",
        )

    request_cost = Decimal(str(usage.cost)) if usage.cost is not None else Decimal("0")
    try:
        updated_budget = await usage_ledger.record(
            principal.client_id,
            usage_budget,
            total_tokens=usage.total_tokens,
            cost_usd=request_cost,
        )
    except UsageLedgerUnavailable as exc:
        emit_audit_event(
            request_id=request_id,
            event="usage_budget",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            provider=provider.name,
            request_tokens=usage.total_tokens,
            request_cost_usd=usage.cost,
            reason="usage_ledger_write_failed",
        )
        raise HTTPException(
            status_code=503,
            detail="Usage accounting is unavailable.",
        ) from exc

    outgoing_response.headers["X-Usage-Budget-Reset"] = updated_budget.reset_at.isoformat()
    outgoing_response.headers["X-Usage-Tokens-Used"] = str(updated_budget.tokens_used_daily)
    if updated_budget.tokens_remaining_daily is not None:
        outgoing_response.headers["X-Usage-Tokens-Remaining"] = str(
            updated_budget.tokens_remaining_daily
        )
    if usage.cost is not None:
        outgoing_response.headers["X-Usage-Cost-USD"] = str(
            updated_budget.cost_used_daily_usd
        )
        if updated_budget.cost_remaining_daily_usd is not None:
            outgoing_response.headers["X-Usage-Cost-Remaining-USD"] = str(
                updated_budget.cost_remaining_daily_usd
            )

    emit_audit_event(
        request_id=request_id,
        event="usage_budget",
        outcome="recorded",
        client_id=principal.client_id,
        key_id=principal.key_id,
        provider=provider.name,
        reason=updated_budget.reason,
        request_tokens=usage.total_tokens,
        request_cost_usd=usage.cost,
        **_budget_audit_fields(
            updated_budget,
            include_cost=usage.cost is not None,
        ),
    )

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
