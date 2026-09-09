from fastapi.testclient import TestClient

from app.main import app


def test_chat_rejects_disallowed_model(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway client authentication and model policy are configured.

    Modifies:
        - Temporarily configures SAG_ALLOWED_MODELS.

    Effects:
        - Sends an authenticated request for a model outside the allowlist.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether model access is denied correctly.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "restricted-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Requested model is not allowed."}


def test_chat_fails_closed_without_model_policy(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway client authentication is configured.

    Modifies:
        - Ensures SAG_ALLOWED_MODELS is absent for this test process.

    Effects:
        - Verifies the gateway does not forward requests without model policy.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether missing policy fails closed.
    """
    monkeypatch.delenv("SAG_ALLOWED_MODELS", raising=False)

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Model access policy is not configured."}
