from fastapi.testclient import TestClient

from app.main import app


def test_chat_completion(monkeypatch) -> None:
    """
    RME

    Requires:
        - The FastAPI application and fake provider can be imported.

    Modifies:
        - Temporarily configures SAG_API_KEY and SAG_ALLOWED_MODELS.

    Effects:
        - Exercises an authenticated and policy-approved chat-completion request.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - None. Assertions determine whether the test passes.
    """
    monkeypatch.setenv("SAG_API_KEY", "sag_test_key")
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "fake-model")

    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer sag_test_key",
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

    assert response.status_code == 200

    body = response.json()

    assert body["object"] == "chat.completion"
    assert body["model"] == "fake-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Fake provider response."
