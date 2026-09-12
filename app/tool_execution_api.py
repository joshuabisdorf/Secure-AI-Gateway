import binascii
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST

from app.api_keys import Principal
from app.audit import emit_audit_event
from app.auth import authenticate_api_key
from app.models import (
    ToolExecutionAuthorizationRequest,
    ToolExecutionAuthorizationResponse,
)
from app.observability import metrics_payload
from app.tool_authorization import get_client_allowed_tools
from app.tool_execution import (
    ToolExecutionRejected,
    ToolExecutionUnavailable,
    build_tool_execution_replay_store,
    verify_execution_ticket,
)

router = APIRouter()
tool_execution_replay_store = build_tool_execution_replay_store()


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    """
    RME

    Requires:
        - The endpoint is exposed only on a trusted/internal network boundary in production.

    Modifies:
        - Nothing.

    Effects:
        - Serializes the dedicated Secure AI Gateway Prometheus registry.
        - Does not expose prompts, credentials, client IDs, request IDs, model names, tool names,
          tool arguments, or tool results as metric labels.

    Inputs:
        - None.

    Outputs:
        - Prometheus text exposition response.
    """
    return Response(
        content=metrics_payload(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


def _deny_execution(
    *,
    request_id: str,
    principal: Principal,
    reason: str,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    risk: str | None = None,
    source_request_id: str | None = None,
    status_code: int = status.HTTP_403_FORBIDDEN,
) -> None:
    """
    RME

    Requires:
        - principal is authenticated and reason contains no tool arguments/tickets.

    Modifies:
        - Audit logging stream.

    Effects:
        - Emits a metadata-only execution denial and raises a generic HTTP error.

    Inputs:
        - request_id: Authorization request correlation ID.
        - principal: Current authenticated client/key identity.
        - reason: Safe internal denial reason.
        - tool_name: Optional validated function name.
        - tool_call_id: Optional provider tool-call ID.
        - risk: Optional read/write/destructive classification.
        - source_request_id: Optional source chat request ID.
        - status_code: HTTP status returned to the caller.

    Outputs:
        - None; always raises HTTPException.
    """
    emit_audit_event(
        request_id=request_id,
        event="tool_execution_authorization",
        outcome="deny",
        client_id=principal.client_id,
        key_id=principal.key_id,
        reason=reason,
        tool_requested_names=tool_name,
        tool_call_id=tool_call_id,
        tool_execution_risk=risk,
        source_request_id=source_request_id,
    )
    detail = (
        "Tool execution authorization has already been used."
        if status_code == status.HTTP_409_CONFLICT
        else "Tool execution is not authorized."
    )
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"X-Tool-Execution-Authorization": "denied"},
    )


@router.post(
    "/v1/tool-executions/authorize",
    response_model=ToolExecutionAuthorizationResponse,
)
async def authorize_tool_execution(
    authorization_request: ToolExecutionAuthorizationRequest,
    http_request: Request,
    outgoing_response: Response,
    principal: Principal = Depends(authenticate_api_key),
) -> ToolExecutionAuthorizationResponse:
    """
    RME

    Requires:
        - Caller authenticates with the same gateway client/key that received the ticket.
        - tool_call is the exact model-generated call returned by the gateway.
        - Current client tool policy, execution registry, signing key, and replay store are available.

    Modifies:
        - Distributed/process-local replay state by atomically consuming one execution ID.
        - Audit logging stream and response authorization header.

    Effects:
        - Re-authenticates execution context independently of model output.
        - Verifies HMAC ticket integrity, expiry, identity, exact arguments, schema, and risk class.
        - Rejects malformed ticket encoding as a controlled authorization denial.
        - Re-checks current per-client tool authorization so revoked tools cannot execute on stale tickets.
        - Consumes each execution authorization exactly once before returning allow.
        - Never logs or returns raw tool arguments or execution-ticket contents.

    Inputs:
        - authorization_request: Exact gateway-returned tool call including execution token/risk.
        - http_request: HTTP request with correlation context.
        - outgoing_response: Response used for safe authorization headers.
        - principal: Current independently authenticated client identity.

    Outputs:
        - Metadata-only authorization response for a downstream executor.
    """
    request_id = http_request.state.request_id
    tool_call = authorization_request.tool_call
    token = tool_call.execution_token
    if not token:
        _deny_execution(
            request_id=request_id,
            principal=principal,
            reason="missing_execution_ticket",
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
        )

    try:
        ticket = verify_execution_ticket(
            token,
            tool_call,
            client_id=principal.client_id,
            key_id=principal.key_id,
        )
    except ToolExecutionUnavailable as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_authorization",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=exc.reason,
            tool_requested_names=tool_call.function.name,
            tool_call_id=tool_call.id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool execution authorization is unavailable.",
        ) from exc
    except ToolExecutionRejected as exc:
        _deny_execution(
            request_id=request_id,
            principal=principal,
            reason=exc.reason,
            tool_name=exc.tool_name or tool_call.function.name,
            tool_call_id=tool_call.id,
            risk=exc.risk,
        )
    except binascii.Error:
        _deny_execution(
            request_id=request_id,
            principal=principal,
            reason="invalid_execution_ticket",
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
        )

    if tool_call.execution_risk != ticket.risk:
        _deny_execution(
            request_id=request_id,
            principal=principal,
            reason="tool_execution_risk_mismatch",
            tool_name=ticket.tool_name,
            tool_call_id=ticket.tool_call_id,
            risk=ticket.risk,
            source_request_id=ticket.source_request_id,
        )

    try:
        allowed_tools = get_client_allowed_tools(principal.client_id)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_authorization",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=str(exc.detail),
            tool_requested_names=ticket.tool_name,
            tool_call_id=ticket.tool_call_id,
            tool_execution_id=ticket.execution_id,
            tool_execution_risk=ticket.risk,
            source_request_id=ticket.source_request_id,
        )
        raise

    if ticket.tool_name not in allowed_tools:
        _deny_execution(
            request_id=request_id,
            principal=principal,
            reason="tool_not_allowed_at_execution",
            tool_name=ticket.tool_name,
            tool_call_id=ticket.tool_call_id,
            risk=ticket.risk,
            source_request_id=ticket.source_request_id,
        )

    ttl_seconds = max(1, ticket.expires_at - int(time.time()))
    try:
        claimed = await tool_execution_replay_store.claim(
            ticket.execution_id,
            ttl_seconds,
        )
    except ToolExecutionUnavailable as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_execution_authorization",
            outcome="error",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=exc.reason,
            tool_requested_names=ticket.tool_name,
            tool_call_id=ticket.tool_call_id,
            tool_execution_id=ticket.execution_id,
            tool_execution_risk=ticket.risk,
            source_request_id=ticket.source_request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool execution authorization is unavailable.",
        ) from exc

    if not claimed:
        _deny_execution(
            request_id=request_id,
            principal=principal,
            reason="execution_ticket_replayed",
            tool_name=ticket.tool_name,
            tool_call_id=ticket.tool_call_id,
            risk=ticket.risk,
            source_request_id=ticket.source_request_id,
            status_code=status.HTTP_409_CONFLICT,
        )

    outgoing_response.headers["X-Tool-Execution-Authorization"] = "allowed"
    emit_audit_event(
        request_id=request_id,
        event="tool_execution_authorization",
        outcome="allow",
        client_id=principal.client_id,
        key_id=principal.key_id,
        tool_requested_names=ticket.tool_name,
        tool_call_id=ticket.tool_call_id,
        tool_execution_id=ticket.execution_id,
        tool_execution_risk=ticket.risk,
        source_request_id=ticket.source_request_id,
    )
    return ToolExecutionAuthorizationResponse(
        execution_id=ticket.execution_id,
        source_request_id=ticket.source_request_id,
        tool_name=ticket.tool_name,
        risk=ticket.risk,
    )
