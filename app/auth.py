import hmac
import os

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


def authenticate_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> str:
    """
    RME

    Requires:
        - SAG_API_KEY is configured in the gateway environment.
        - The caller may provide an Authorization bearer token.

    Modifies:
        - Nothing.

    Effects:
        - Rejects requests with missing or invalid gateway credentials.

    Inputs:
        - credentials: Bearer credentials extracted from the request.

    Outputs:
        - The authenticated API key when authentication succeeds.
    """
    expected_key = os.getenv("SAG_API_KEY")

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway authentication is not configured.",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(credentials.credentials, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials