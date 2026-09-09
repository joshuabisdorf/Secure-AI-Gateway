from fastapi import Depends, FastAPI

from app.auth import authenticate_api_key
from app.models import ChatCompletionRequest, ChatCompletionResponse
from app.providers.fake import FakeProvider

app = FastAPI(
    title="Secure AI Gateway",
    version="0.1.0",
)

provider = FakeProvider()


@app.get("/health")
def health() -> dict[str, str]:
    """
    RME

    Requires:
        - The FastAPI application is running.

    Modifies:
        - Nothing.

    Effects:
        - Reports the health status of the gateway.

    Inputs:
        - None.

    Outputs:
        - A dictionary containing the gateway health status.
    """
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completion(
    request: ChatCompletionRequest,
    _: str = Depends(authenticate_api_key),
) -> ChatCompletionResponse:
    """
    RME

    Requires:
        - request satisfies the gateway chat-completion schema.
        - The caller provides a valid gateway API key.

    Modifies:
        - Provider-specific state, if any.

    Effects:
        - Authenticates the caller.
        - Sends the normalized request to the configured provider.

    Inputs:
        - request: Requested model and chat messages.
        - _: Authenticated gateway credential supplied by dependency injection.

    Outputs:
        - An OpenAI-style chat-completion response.
    """
    return await provider.chat_completion(request)