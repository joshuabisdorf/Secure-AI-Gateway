import json
import logging

from fastapi.testclient import TestClient

from app.audit import audit_logger
from app.main import app


def _audit_events(caplog) -> list[dict[str, object]]:
    """
    RME

    Requires:
        - caplog contains logging records captured by pytest.

    Modifies:
        - Nothing.

    Effects:
        - Filters and parses structured Secure AI Gateway audit records.

    Inputs:
        - caplog: pytest log-capture fixture.

    Outputs:
        - Parsed JSON audit event dictionaries.
    """
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "secure_ai_gateway.audit"
    ]


def test_audit_log_attributes_client_without_secrets(
    monkeypatch,
    caplog,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway client authentication, rate-limit policy, and model policy are configured.

    Modifies:
        - Temporarily configures gateway environment variables and log capture.
        - Temporarily attaches pytest's capture handler to the audit logger.

    Effects:
        - Sends a valid request and verifies structured security audit events.
        - Verifies client identity, rate-limit state, and model identities are recorded.
        - Verifies prompt content and bearer credentials are not logged.

    Inputs:
        - monkeypatch: pytest environment fixture.
        - caplog: pytest log-capture fixture.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether auditing is safe and complete.
    """
    monkeypatch.setenv("SAG_CLIENT_RATE_LIMITS", "test-client:10")
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv(
        "SAG_CLIENT_ALLOWED_MODELS",
        "test-client:mock-model",
    )
    caplog.set_level(logging.INFO, logger="secure_ai_gateway.audit")

    client = TestClient(app)
    secret_prompt = "do not log this prompt value"

    audit_logger.addHandler(caplog.handler)
    try:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {gateway_api_key}"},
            json={
                "model": "mock-model",
                "messages": [{"role": "user", "content": secret_prompt}],
            },
        )
    finally:
        audit_logger.removeHandler(caplog.handler)

    assert response.status_code == 200
    assert response.headers["X-Request-ID"].startswith("req_")

    events = _audit_events(caplog)
    authentication_event = next(
        event for event in events if event["event"] == "authentication"
    )
    assert authentication_event["outcome"] == "allow"
    assert authentication_event["client_id"] == "test-client"
    assert authentication_event["key_id"] == "testkey"

    rate_limit_event = next(event for event in events if event["event"] == "rate_limit")
    assert rate_limit_event["outcome"] == "allow"
    assert rate_limit_event["client_id"] == "test-client"
    assert rate_limit_event["key_id"] == "testkey"
    assert rate_limit_event["limit_rpm"] == 10
    assert rate_limit_event["remaining"] == 9

    policy_event = next(event for event in events if event["event"] == "model_policy")
    assert policy_event["outcome"] == "allow"
    assert policy_event["client_id"] == "test-client"
    assert policy_event["key_id"] == "testkey"
    assert policy_event["requested_model"] == "mock-model"
    assert "resolved_model" not in policy_event

    completion_event = next(
        event for event in events if event["event"] == "chat_completion"
    )
    assert completion_event["outcome"] == "success"
    assert completion_event["client_id"] == "test-client"
    assert completion_event["key_id"] == "testkey"
    assert completion_event["requested_model"] == "mock-model"
    assert completion_event["resolved_model"] == "mock-model"
    assert completion_event["request_id"] == response.headers["X-Request-ID"]
    assert float(completion_event["latency_ms"]) >= 0

    serialized_events = "\n".join(record.message for record in caplog.records)
    assert secret_prompt not in serialized_events
    assert gateway_api_key not in serialized_events


def test_request_id_header_is_preserved() -> None:
    """
    RME

    Requires:
        - The gateway application can receive HTTP requests.

    Modifies:
        - Nothing.

    Effects:
        - Verifies a valid caller-provided request ID is returned unchanged.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether request correlation works.
    """
    client = TestClient(app)
    response = client.get(
        "/health",
        headers={"X-Request-ID": "req-client-123"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-client-123"
