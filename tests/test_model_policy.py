from fastapi.testclient import TestClient

from app.main import app


def test_chat_rejects_disallowed_model(monkeypatch) -> None:
    """
    RME

    Requires:
        - Gateway authentication and model policy are configured.

    Modifies:
        - Temporarily configures SAG_API_KEY and SAG_ALLOWED_MODELS.

    Effects:
        - Sends an authenticated request for a model outside the allowlist.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - None. Assertions determine whether model access is denied correctly.
    """
    monkeypatch.setenv("SAG_API_KEY", "sag_test_key")
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "fake-model")

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sag_test_key"},
        json={
            "model": "restricted-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Requested model is not allowed."}


def test_chat_fails_closed_without_model_policy(monkeypatch) -> None:
    """
    RME

    Requires:
        - Gateway authentication is configured.

    Modifies:
        - Temporarily configures SAG_API_KEY.
        - Ensures SAG_ALLOWED_MODELS is absent for this test process.

    Effects:
        - Verifies the gateway does not forward requests without model policy.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - None. Assertions determine whether missing policy fails closed.
    """
    monkeypatch.setenv("SAG_API_KEY", "sag_test_key")
    monkeypatch.delenv("SAG_ALLOWED_MODELS", raising=False)

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sag_test_key"},
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Model access policy is not configured."}
