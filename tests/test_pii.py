import logging

from fastapi.testclient import TestClient

from app import main
from app.audit import audit_logger
from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatUsage,
    ChoiceMessage,
)
from app.pii import inspect_and_redact_request, parse_client_pii_policies
from app.providers.base import Provider


class CapturingProvider(Provider):
    name = "capturing"

    def __init__(self) -> None:
        self.request: ChatCompletionRequest | None = None

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        self.request = request
        return ChatCompletionResponse(
            id="chatcmpl-pii-test",
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChoiceMessage(role="assistant", content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=ChatUsage(
                prompt_tokens=2,
                completion_tokens=1,
                total_tokens=3,
                cost=0.0,
            ),
        )


def _configure_chat_policy(monkeypatch) -> None:
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")


def test_pii_policy_parser_supports_redact_and_deny() -> None:
    """
    RME

    Requires:
        - PII policy records use client_id:action format.

    Modifies:
        - Nothing.

    Effects:
        - Verifies supported PII actions are parsed per client.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether policy parsing is correct.
    """
    policies = parse_client_pii_policies("client-a:redact,client-b:deny")

    assert policies["client-a"].action == "redact"
    assert policies["client-b"].action == "deny"


def test_structured_pii_is_redacted_without_retaining_raw_values() -> None:
    """
    RME

    Requires:
        - The request contains representative structured PII test values.

    Modifies:
        - Nothing.

    Effects:
        - Verifies email, SSN, phone, and Luhn-valid payment-card detection.
        - Verifies the original request remains unchanged.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether structured redaction is correct.
    """
    original = (
        "Contact alice@example.com, SSN 123-45-6789, phone 313-555-0123, "
        "card 4111 1111 1111 1111."
    )
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": original}],
    )

    result = inspect_and_redact_request(request)
    redacted = result.redacted_request.messages[0].content

    assert request.messages[0].content == original
    assert result.detected_count == 4
    assert result.detected_types == ("email", "payment_card", "phone", "ssn")
    assert "alice@example.com" not in redacted
    assert "123-45-6789" not in redacted
    assert "313-555-0123" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_SSN]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert "[REDACTED_PAYMENT_CARD]" in redacted


def test_redact_policy_forwards_only_sanitized_prompt_and_safe_audit(
    monkeypatch,
    caplog,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway authentication, model, rate, usage, and PII policy are configured.

    Modifies:
        - Temporarily replaces the provider with a capturing test provider.
        - Temporarily attaches pytest log capture to the audit logger.
        - Process-local test rate and usage state.

    Effects:
        - Verifies detected PII is replaced before provider forwarding.
        - Verifies safe PII response headers describe the action without raw values.
        - Verifies audit output contains only PII count/type metadata, never the value.

    Inputs:
        - monkeypatch: pytest fixture used to configure policy and provider state.
        - caplog: pytest log-capture fixture.
        - gateway_api_key: Raw test gateway key for the configured client.

    Outputs:
        - None. Assertions determine whether egress and audit handling are safe.
    """
    _configure_chat_policy(monkeypatch)
    monkeypatch.setenv("SAG_CLIENT_PII_POLICIES", "test-client:redact")
    capturing_provider = CapturingProvider()
    monkeypatch.setattr(main, "provider", capturing_provider)
    caplog.set_level(logging.INFO, logger="secure_ai_gateway.audit")

    client = TestClient(main.app)
    audit_logger.addHandler(caplog.handler)
    try:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {gateway_api_key}"},
            json={
                "model": "mock-model",
                "messages": [
                    {
                        "role": "user",
                        "content": "Email me at alice@example.com",
                    }
                ],
            },
        )
    finally:
        audit_logger.removeHandler(caplog.handler)

    assert response.status_code == 200
    assert response.headers["X-PII-Action"] == "redacted"
    assert response.headers["X-PII-Detected-Count"] == "1"
    assert capturing_provider.request is not None
    forwarded = capturing_provider.request.messages[0].content
    assert forwarded == "Email me at [REDACTED_EMAIL]"
    assert "alice@example.com" not in forwarded

    pii_records = [
        record.message
        for record in caplog.records
        if record.name == "secure_ai_gateway.audit" and '"event":"pii_policy"' in record.message
    ]
    assert len(pii_records) == 1
    assert '"outcome":"redact"' in pii_records[0]
    assert '"pii_detected_count":1' in pii_records[0]
    assert '"pii_types":"email"' in pii_records[0]
    assert "alice@example.com" not in "\n".join(record.message for record in caplog.records)


def test_deny_policy_blocks_pii_before_provider(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway authentication and model policy are configured.
        - The client PII action is deny.

    Modifies:
        - Temporarily replaces the provider with a capturing test provider.

    Effects:
        - Verifies PII causes a generic 403 without provider forwarding.

    Inputs:
        - monkeypatch: pytest fixture used to configure policy and provider state.
        - gateway_api_key: Raw test gateway key for the configured client.

    Outputs:
        - None. Assertions determine whether deny mode prevents egress.
    """
    _configure_chat_policy(monkeypatch)
    monkeypatch.setenv("SAG_CLIENT_PII_POLICIES", "test-client:deny")
    capturing_provider = CapturingProvider()
    monkeypatch.setattr(main, "provider", capturing_provider)

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [
                {
                    "role": "user",
                    "content": "My SSN is 123-45-6789",
                }
            ],
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Request contains prohibited sensitive data."}
    assert response.headers["X-PII-Action"] == "denied"
    assert response.headers["X-PII-Detected-Count"] == "1"
    assert capturing_provider.request is None


def test_chat_fails_closed_without_pii_policy(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway authentication and model policy are configured.

    Modifies:
        - Temporarily removes SAG_CLIENT_PII_POLICIES.

    Effects:
        - Verifies protected requests do not reach a provider without PII policy.

    Inputs:
        - monkeypatch: pytest fixture used to remove policy configuration.
        - gateway_api_key: Raw test gateway key for the configured client.

    Outputs:
        - None. Assertions determine whether missing PII policy fails closed.
    """
    _configure_chat_policy(monkeypatch)
    monkeypatch.delenv("SAG_CLIENT_PII_POLICIES", raising=False)
    capturing_provider = CapturingProvider()
    monkeypatch.setattr(main, "provider", capturing_provider)

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "PII policy is not configured."}
    assert capturing_provider.request is None
