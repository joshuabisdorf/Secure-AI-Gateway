import hmac

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api_keys import Principal, hash_api_key, parse_key_id
from app.audit import emit_audit_event
from app.client_registry import ClientRegistryUnavailable, build_client_registry

bearer_scheme = HTTPBearer(auto_error=False)
client_registry = build_client_registry()


async def authenticate_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> Principal:
    """
    RME

    Requires:
        - A usable gateway client registry backend is configured.
        - The caller may provide an Authorization bearer token.
        - Request middleware has assigned a request ID.

    Modifies:
        - Client-registry connection/query state, if any.
        - The audit logging stream.

    Effects:
        - Resolves a structured gateway API key to an active client identity.
        - Compares a one-way hash of the presented key with the stored hash.
        - Records the authentication decision without logging the raw credential.
        - Rejects missing, invalid, inactive, or unavailable gateway credentials.

    Inputs:
        - request: HTTP request containing gateway request context.
        - credentials: Bearer credentials extracted from the request.

    Outputs:
        - Authenticated Principal containing client_id and key_id.
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

    principal = Principal(
        client_id=record.client_id,
        key_id=record.key_id,
    )
    emit_audit_event(
        request_id=request_id,
        event="authentication",
        outcome="allow",
        client_id=principal.client_id,
        key_id=principal.key_id,
    )
    return principal
