from fastapi.testclient import TestClient

from app.main import app


def test_chat_completion(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - The FastAPI application and mock provider can be imported.
        - Gateway client authentication is configured.

    Modifies:
        - Temporarily configures SAG_ALLOWED_MODELS.

    Effects:
        - Exercises an identified, authenticated, and policy-approved chat request.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether the test passes.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {gateway_api_key}",
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

    assert response.status_code == 200

    body = response.json()

    assert body["object"] == "chat.completion"
    assert body["model"] == "mock-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Mock provider response."
