from fastapi.testclient import TestClient

from app.main import app


def test_chat_rejects_missing_api_key(monkeypatch) -> None:
    """
    RME

    Requires:
        - Gateway authentication is configured.

    Modifies:
        - Temporarily configures SAG_API_KEY.

    Effects:
        - Sends an unauthenticated request to a protected endpoint.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - None. Assertions determine whether authentication fails correctly.
    """
    monkeypatch.setenv("SAG_API_KEY", "sag_test_key")

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fake-model",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        },
    )

    assert response.status_code == 401


def test_chat_rejects_invalid_api_key(monkeypatch) -> None:
    """
    RME

    Requires:
        - Gateway authentication is configured.

    Modifies:
        - Temporarily configures SAG_API_KEY.

    Effects:
        - Sends a request containing an invalid gateway API key.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - None. Assertions determine whether authentication fails correctly.
    """
    monkeypatch.setenv("SAG_API_KEY", "sag_test_key")

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer wrong-key",
        },
        json={
            "model": "fake-model",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        },
    )

    assert response.status_code == 401