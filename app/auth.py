import hmac
import os

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.audit import emit_audit_event

bearer_scheme = HTTPBearer(auto_error=False)


def authenticate_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> str:
    """
    RME

    Requires:
        - SAG_API_KEY is configured in the gateway environment.
        - The caller may provide an Authorization bearer token.
        - Request middleware has assigned a request ID.

    Modifies:
        - The audit logging stream.

    Effects:
        - Records the authentication decision.
        - Rejects requests with missing or invalid gateway credentials.

    Inputs:
        - request: HTTP request containing gateway request context.
        - credentials: Bearer credentials extracted from the request.

    Outputs:
        - The authenticated API key when authentication succeeds.
    """
    request_id = request.state.request_id
    expected_key = os.getenv("SAG_API_KEY")

    if not expected_key:
        emit_audit_event(
            request_id=request_id,
            event="authentication",
            outcome="deny",
            reason="not_configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway authentication is not configured.",
        )

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

    if not hmac.compare_digest(credentials.credentials, expected_key):
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

    emit_audit_event(
        request_id=request_id,
        event="authentication",
        outcome="allow",
    )
    return credentials.credentials
