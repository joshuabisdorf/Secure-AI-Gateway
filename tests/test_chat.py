from fastapi.testclient import TestClient

from app.main import app


def test_chat_completion() -> None:
    """
    RME

    Requires:
        - The FastAPI application and fake provider can be imported.

    Modifies:
        - Nothing.

    Effects:
        - Exercises the chat-completion API without an external LLM call.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether the test passes.
    """
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

    assert response.status_code == 200

    body = response.json()

    assert body["object"] == "chat.completion"
    assert body["model"] == "fake-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Fake provider response."