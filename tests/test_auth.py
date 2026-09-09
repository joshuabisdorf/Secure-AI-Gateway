from fastapi.testclient import TestClient

from app.main import app


def test_chat_rejects_missing_api_key(gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway client authentication is configured.

    Modifies:
        - Nothing outside the test request.

    Effects:
        - Sends an unauthenticated request to a protected endpoint.

    Inputs:
        - gateway_api_key: Fixture that configures the hashed client registry.

    Outputs:
        - None. Assertions determine whether authentication fails correctly.
    """
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-model",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing API key."}


def test_chat_rejects_invalid_api_key(gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway client authentication is configured.

    Modifies:
        - Nothing outside the test request.

    Effects:
        - Sends a request containing an invalid structured gateway API key.

    Inputs:
        - gateway_api_key: Fixture that configures the hashed client registry.

    Outputs:
        - None. Assertions determine whether authentication fails correctly.
    """
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer sag_testkey_wrong-secret",
        },
        json={
            "model": "mock-model",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key."}


def test_chat_fails_closed_without_client_registry(monkeypatch) -> None:
    """
    RME

    Requires:
        - SAG_CLIENTS can be absent from the test environment.

    Modifies:
        - Temporarily removes SAG_CLIENTS.

    Effects:
        - Verifies protected endpoints fail closed when client identity data is absent.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - None. Assertions determine whether missing authentication config returns 503.
    """
    monkeypatch.delenv("SAG_CLIENTS", raising=False)
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sag_testkey_any-secret"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Gateway authentication is not configured."}
