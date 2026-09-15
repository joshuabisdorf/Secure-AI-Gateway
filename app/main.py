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
from app.pii import get_client_pii_policy, inspect_and_redact_request
from app.policies.model_access import enforce_model_allowed
from app.prompt_injection import (
    get_client_prompt_injection_policy,
    inspect_prompt_injection,
)
from app.providers.base import ProviderError
from app.providers.factory import build_provider
from app.rate_limit import (
    RateLimiterUnavailable,
    build_rate_limiter,
    get_client_rate_limit,
)
from app.tool_authorization import get_client_allowed_tools
from app.tool_execution import (
    ToolExecutionRejected,
    ToolExecutionUnavailable,
    prepare_tool_execution_response,
)
from app.tool_execution_api import (
    router as tool_execution_router,
    tool_execution_replay_store,
)
from app.usage_budget import (
    UsageBudgetDecision,
    UsageLedgerUnavailable,
    build_usage_ledger,
    get_client_usage_budget,
)

provider = build_provider()
rate_limiter = build_rate_limiter()
usage_ledger = build_usage_ledger()
_request_id_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Manage application resource shutdown.

    Requires:
        Configured persistent resources may own async connection pools.
    Modifies:
        Client registry, rate limiter, usage ledger, and replay-store pools.
    Effects:
        Leaves connections lazy at startup and closes opened pools at shutdown.
    Inputs:
        _: FastAPI application instance.
    Outputs:
        Async lifespan context for FastAPI.
    """
    yield

    for resource in (
        client_registry,
        rate_limiter,
        usage_ledger,
        tool_execution_replay_store,
    ):
        close = getattr(resource, "close", None)
        if close is not None:
            await close()


app = FastAPI(
    title="Secure AI Gateway",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(tool_execution_router)


def resolve_request_id(candidate: str | None) -> str:
    """Return a safe request correlation identifier.

    Requires:
        candidate may contain a caller-supplied request identifier.
    Modifies:
        Nothing.
    Effects:
        Generates a new ID when the supplied value is absent or unsafe.
    Inputs:
        candidate: Optional X-Request-ID header value.
    Outputs:
        A validated caller ID or a newly generated gateway request ID.
    """
    if candidate is not None and _request_id_pattern.fullmatch(candidate):
        return candidate
    return f"req_{uuid4().hex}"


def _budget_audit_fields(
    decision: UsageBudgetDecision,
    *,
    include_cost: bool,
) -> dict[str, int | float | str | None]:
    """Convert usage state into safe audit fields.

    Requires:
        decision contains current daily usage state.
        include_cost is true only when cost accounting is known or enforced.
    Modifies:
        Nothing.
    Effects:
        Omits cumulative cost fields when provider cost is not known.
    Inputs:
        decision: Current daily usage-budget decision.
        include_cost: Whether cost accounting should be exposed.
    Outputs:
        Keyword arguments suitable for emit_audit_event.
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
    """Attach request correlation context.

    Requires:
        request is an incoming HTTP request.
        call_next invokes the remaining FastAPI request pipeline.
    Modifies:
        request.state and outgoing response headers.
    Effects:
        Assigns a request ID, starts latency timing, and returns the ID header.
    Inputs:
        request: Incoming HTTP request.
        call_next: Callable for the remaining request pipeline.
    Outputs:
        HTTP response containing X-Request-ID.
    """
    request_id = resolve_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    request.state.started_at = perf_counter()

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """Return process health without consulting security-state backends.

    Requires:
        The process can execute the endpoint.
    Modifies:
        Nothing.
    Effects:
        Reports process liveness only; it does not claim dependency readiness.
    Inputs:
        None.
    Outputs:
        Static health status mapping.
    """
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completion(
    request: ChatCompletionRequest,
    http_request: Request,
    outgoing_response: Response,
    principal: Principal = Depends(authenticate_api_key),
) -> ChatCompletionResponse:
    """Apply gateway policy and return a chat-completion response.

    Requires:
        request satisfies the gateway chat-completion schema.
        The caller has a valid database-backed gateway API key.
        Rate, model, PII, injection, tool, and usage policy are configured.
        Redis rate state and PostgreSQL usage accounting are available.
        Tool execution state is required only for returned tool calls.
    Modifies:
        Shared per-client rate-limit state in Redis.
        A copied provider request when PII is redacted.
        Persistent per-client daily usage totals in PostgreSQL.
        Provider-specific state, audit output, and response policy headers.
    Effects:
        Applies authentication and distributed request throttling.
        Enforces model, PII, prompt-injection, and usage policy.
        Forwards only the policy-processed request to the provider.
        Records provider-reported usage after successful completion.
        Validates untrusted tool calls before issuing short-lived tickets.
        Fails closed when required policy or shared state is unavailable.
    Inputs:
        request: Requested model and chat messages.
        http_request: HTTP request with request ID and timing context.
        outgoing_response: Response used to expose safe policy headers.
        principal: Authenticated client identity from dependency injection.
    Outputs:
        OpenAI-style response with optional tool-execution metadata.
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

    try:
        rate_decision = await rate_limiter.check(
            principal.client_id,
            limit_rpm,
        )
    except RateLimiterUnavailable as exc:
        emit_audit_event(
            request_id=request_id,
            event="rate_limit",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason="rate_limiter_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Rate limiting is unavailable.",
        ) from exc

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

    outgoing_response.headers["X-RateLimit-Limit"] = str(
        rate_decision.limit_rpm
    )
    outgoing_response.headers["X-RateLimit-Remaining"] = str(
        rate_decision.remaining
    )
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
        pii_policy = get_client_pii_policy(principal.client_id)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="pii_policy",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            requested_model=request.model,
            reason=str(exc.detail),
        )
        raise

    pii_result = inspect_and_redact_request(request)
    pii_types = ",".join(pii_result.detected_types) or None

    if pii_result.detected_count > 0 and pii_policy.action == "deny":
        emit_audit_event(
            request_id=request_id,
            event="pii_policy",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            requested_model=request.model,
            reason="pii_detected",
            pii_detected_count=pii_result.detected_count,
            pii_types=pii_types,
        )
        raise HTTPException(
            status_code=403,
            detail="Request contains prohibited sensitive data.",
            headers={
                "X-PII-Action": "denied",
                "X-PII-Detected-Count": str(pii_result.detected_count),
            },
        )

    provider_request = request
    pii_outcome = "allow"
    pii_action_header = "none"
    if pii_result.detected_count > 0:
        provider_request = pii_result.redacted_request
        pii_outcome = "redact"
        pii_action_header = "redacted"

    outgoing_response.headers["X-PII-Action"] = pii_action_header
    outgoing_response.headers["X-PII-Detected-Count"] = str(
        pii_result.detected_count
    )
    emit_audit_event(
        request_id=request_id,
        event="pii_policy",
        outcome=pii_outcome,
        client_id=principal.client_id,
        key_id=principal.key_id,
        requested_model=request.model,
        pii_detected_count=pii_result.detected_count,
        pii_types=pii_types,
    )

    try:
        prompt_injection_policy = get_client_prompt_injection_policy(
            principal.client_id
        )
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="prompt_injection",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            requested_model=request.model,
            reason=str(exc.detail),
        )
        raise

    if prompt_injection_policy.action == "off":
        injection_detected_count = 0
        injection_score = 0
        injection_indicators: tuple[str, ...] = ()
        injection_outcome = "off"
        injection_action_header = "off"
    else:
        injection_result = inspect_prompt_injection(provider_request)
        injection_detected_count = injection_result.detected_count
        injection_score = injection_result.score
        injection_indicators = injection_result.indicators

        if (
            injection_detected_count > 0
            and prompt_injection_policy.action == "deny"
        ):
            indicator_names = ",".join(injection_indicators) or None
            emit_audit_event(
                request_id=request_id,
                event="prompt_injection",
                outcome="deny",
                client_id=principal.client_id,
                key_id=principal.key_id,
                requested_model=request.model,
                reason="prompt_injection_detected",
                prompt_injection_detected_count=injection_detected_count,
                prompt_injection_score=injection_score,
                prompt_injection_indicators=indicator_names,
            )
            raise HTTPException(
                status_code=403,
                detail="Potential prompt injection detected.",
                headers={
                    "X-Prompt-Injection-Action": "denied",
                    "X-Prompt-Injection-Detected-Count": str(
                        injection_detected_count
                    ),
                    "X-Prompt-Injection-Score": str(injection_score),
                },
            )

        injection_outcome = (
            "audit" if injection_detected_count > 0 else "allow"
        )
        injection_action_header = (
            "audited" if injection_detected_count > 0 else "none"
        )

    indicator_names = ",".join(injection_indicators) or None
    outgoing_response.headers["X-Prompt-Injection-Action"] = (
        injection_action_header
    )
    outgoing_response.headers["X-Prompt-Injection-Detected-Count"] = str(
        injection_detected_count
    )
    outgoing_response.headers["X-Prompt-Injection-Score"] = str(
        injection_score
    )
    emit_audit_event(
        request_id=request_id,
        event="prompt_injection",
        outcome=injection_outcome,
        client_id=principal.client_id,
        key_id=principal.key_id,
        requested_model=request.model,
        reason=(
            "prompt_injection_detected"
            if injection_detected_count > 0
            else None
        ),
        prompt_injection_detected_count=injection_detected_count,
        prompt_injection_score=injection_score,
        prompt_injection_indicators=indicator_names,
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
        budget_decision = await usage_ledger.check(
            principal.client_id,
            usage_budget,
        )
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
                include_cost=(
                    usage_budget.cost_limit_daily_usd is not None
                ),
            ),
        )
        raise HTTPException(
            status_code=403,
            detail="Usage budget exceeded.",
            headers={
                "X-Usage-Budget-Reset": (
                    budget_decision.reset_at.isoformat()
                )
            },
        )

    try:
        provider_response = await provider.chat_completion(provider_request)
    except ProviderError as exc:
        latency_ms = (
            perf_counter() - http_request.state.started_at
        ) * 1000
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
        latency_ms = (
            perf_counter() - http_request.state.started_at
        ) * 1000
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

    request_cost = (
        Decimal(str(usage.cost))
        if usage.cost is not None
        else Decimal("0")
    )
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

    outgoing_response.headers["X-Usage-Budget-Reset"] = (
        updated_budget.reset_at.isoformat()
    )
    outgoing_response.headers["X-Usage-Tokens-Used"] = str(
        updated_budget.tokens_used_daily
    )
    if updated_budget.tokens_remaining_daily is not None:
        outgoing_response.headers["X-Usage-Tokens-Remaining"] = str(
            updated_budget.tokens_remaining_daily
        )
    if usage.cost is not None:
        outgoing_response.headers["X-Usage-Cost-USD"] = str(
            updated_budget.cost_used_daily_usd
        )
        if updated_budget.cost_remaining_daily_usd is not None:
            outgoing_response.headers[
                "X-Usage-Cost-Remaining-USD"
            ] = str(updated_budget.cost_remaining_daily_usd)

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

    try:
        allowed_tools = get_client_allowed_tools(principal.client_id)
        prepared_tool_response = prepare_tool_execution_response(
            provider_response,
            provider_request,
            client_id=principal.client_id,
            key_id=principal.key_id,
            source_request_id=request_id,
            allowed_tools=allowed_tools,
        )
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_ticket",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=str(exc.detail),
        )
        raise
    except ToolExecutionUnavailable as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_ticket",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=exc.reason,
        )
        raise HTTPException(
            status_code=503,
            detail="Tool execution authorization is unavailable.",
        ) from exc
    except ToolExecutionRejected as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_ticket",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=exc.reason,
            tool_requested_names=exc.tool_name,
            tool_execution_risk=exc.risk,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Upstream provider returned an unauthorized tool call."
            ),
        ) from exc

    provider_response = prepared_tool_response.response
    outgoing_response.headers["X-Tool-Execution-Ticket-Count"] = str(
        len(prepared_tool_response.executions)
    )
    for execution in prepared_tool_response.executions:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_ticket",
            outcome="issued",
            client_id=principal.client_id,
            key_id=principal.key_id,
            tool_requested_names=execution.tool_name,
            tool_call_id=execution.tool_call_id,
            tool_execution_id=execution.execution_id,
            tool_execution_risk=execution.risk,
            source_request_id=request_id,
        )

    latency_ms = (
        perf_counter() - http_request.state.started_at
    ) * 1000
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
