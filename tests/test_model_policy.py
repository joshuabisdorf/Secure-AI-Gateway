from fastapi.testclient import TestClient

from app.main import app
from app.policies.model_access import parse_client_allowed_models


def test_chat_rejects_globally_disallowed_model(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway client authentication and client model policy are configured.

    Modifies:
        - Temporarily configures global and per-client model policy.

    Effects:
        - Verifies the deployment-wide allowlist remains a hard ceiling.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether model access is denied correctly.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv(
        "SAG_CLIENT_ALLOWED_MODELS",
        "test-client:restricted-model",
    )

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


def test_chat_rejects_model_not_granted_to_client(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - The requested model is globally allowed.
        - The authenticated client has a narrower model grant.

    Modifies:
        - Temporarily configures global and per-client model policy.

    Effects:
        - Verifies a client cannot use every globally available model.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether client authorization is enforced.
    """
    monkeypatch.setenv(
        "SAG_ALLOWED_MODELS",
        "mock-model,restricted-model",
    )
    monkeypatch.setenv(
        "SAG_CLIENT_ALLOWED_MODELS",
        "test-client:mock-model",
    )

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


def test_chat_fails_closed_without_global_model_policy(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway client authentication is configured.

    Modifies:
        - Removes SAG_ALLOWED_MODELS and configures a client model grant.

    Effects:
        - Verifies the gateway does not forward requests without global policy.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether missing policy fails closed.
    """
    monkeypatch.delenv("SAG_ALLOWED_MODELS", raising=False)
    monkeypatch.setenv(
        "SAG_CLIENT_ALLOWED_MODELS",
        "test-client:mock-model",
    )

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


def test_chat_fails_closed_without_client_model_policy(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway client authentication and global model policy are configured.

    Modifies:
        - Removes SAG_CLIENT_ALLOWED_MODELS for the current test.

    Effects:
        - Verifies requests are not forwarded without per-client authorization policy.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether missing client policy fails closed.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.delenv("SAG_CLIENT_ALLOWED_MODELS", raising=False)

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
    assert response.json() == {
        "detail": "Client model access policy is not configured."
    }


def test_client_policy_parser_supports_multiple_clients_and_colons() -> None:
    """
    RME

    Requires:
        - Client policy records use client_id:model syntax.

    Modifies:
        - Nothing.

    Effects:
        - Verifies repeated client IDs create multi-model grants.
        - Verifies model slugs may contain colons, such as OpenRouter :free names.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether policy parsing works correctly.
    """
    policies = parse_client_allowed_models(
        "alice:openrouter/free,"
        "alice:nvidia/model:free,"
        "bob:mock-model"
    )

    assert policies["alice"] == frozenset(
        {"openrouter/free", "nvidia/model:free"}
    )
    assert policies["bob"] == frozenset({"mock-model"})
