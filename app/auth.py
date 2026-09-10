import hmac

from fastapi import HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from app.api_keys import Principal, hash_api_key, parse_key_id
from app.audit import emit_audit_event
from app.client_registry import ClientRegistryUnavailable, build_client_registry
from app.models import ChatCompletionRequest
from app.tool_authorization import authorize_request_tools, get_client_allowed_tools

bearer_scheme = HTTPBearer(auto_error=False)
client_registry = build_client_registry()


async def authenticate_api_key(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> Principal:
    """
    RME

    Requires:
        - A usable gateway client registry backend is configured.
        - The caller may provide an Authorization bearer token.
        - Request middleware has assigned a request ID.
        - Tool authorization policy is configured for authenticated chat clients.

    Modifies:
        - Client-registry connection/query state, if any.
        - The audit logging stream and safe tool-authorization response headers.

    Effects:
        - Resolves a structured gateway API key to an active client identity.
        - Compares a one-way hash of the presented key with the stored hash.
        - For valid chat request bodies, enforces least-privilege function-tool exposure.
        - Leaves invalid request-body handling to FastAPI's normal schema validation.
        - Records decisions without logging credentials, tool arguments, or tool outputs.

    Inputs:
        - request: HTTP request containing gateway request context and cached body bytes.
        - response: HTTP response used for safe tool-authorization headers.
        - credentials: Bearer credentials extracted from the request.

    Outputs:
        - Authenticated Principal containing client_id and key_id after applicable tool authorization.
    """
    request_id = request.state.request_id

    if credentials is None:
        emit_audit_event(
            request_id=request_id,
            event="authentication",
            outcome="deny",
            reason="missing_key",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented_key = credentials.credentials
    key_id = parse_key_id(presented_key)
    if key_id is None:
        emit_audit_event(
            request_id=request_id,
            event="authentication",
            outcome="deny",
            reason="invalid_key",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        record = await client_registry.get_key_record(key_id)
    except ClientRegistryUnavailable:
        emit_audit_event(
            request_id=request_id,
            event="authentication",
            outcome="deny",
            reason="client_registry_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway authentication is not configured.",
        ) from None

    if record is None or not hmac.compare_digest(
        hash_api_key(presented_key),
        record.api_key_sha256,
    ):
        emit_audit_event(
            request_id=request_id,
            event="authentication",
            outcome="deny",
            reason="invalid_key",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    principal = Principal(client_id=record.client_id, key_id=record.key_id)
    emit_audit_event(
        request_id=request_id,
        event="authentication",
        outcome="allow",
        client_id=principal.client_id,
        key_id=principal.key_id,
    )

    try:
        body = await request.json()
        chat_request = ChatCompletionRequest.model_validate(body)
    except (ValueError, TypeError, ValidationError):
        return principal

    try:
        allowed_tools = get_client_allowed_tools(principal.client_id)
    except HTTPException as exc:
        emit_audit_event(
            request_id=request_id,
            event="tool_authorization",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=str(exc.detail),
        )
        raise

    decision = authorize_request_tools(chat_request, allowed_tools)
    requested_names = ",".join(decision.requested_tools) or None
    denied_names = ",".join(decision.denied_tools) or None

    if not decision.allowed:
        emit_audit_event(
            request_id=request_id,
            event="tool_authorization",
            outcome="deny",
            client_id=principal.client_id,
            key_id=principal.key_id,
            reason=decision.reason,
            tool_requested_count=len(decision.requested_tools),
            tool_requested_names=requested_names,
            tool_denied_names=denied_names,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requested tool is not allowed.",
            headers={
                "X-Tool-Authorization-Action": "denied",
                "X-Tool-Requested-Count": str(len(decision.requested_tools)),
            },
        )

    action = "allowed" if decision.requested_tools else "none"
    response.headers["X-Tool-Authorization-Action"] = action
    response.headers["X-Tool-Requested-Count"] = str(len(decision.requested_tools))
    emit_audit_event(
        request_id=request_id,
        event="tool_authorization",
        outcome="allow",
        client_id=principal.client_id,
        key_id=principal.key_id,
        tool_requested_count=len(decision.requested_tools),
        tool_requested_names=requested_names,
    )
    return principal
