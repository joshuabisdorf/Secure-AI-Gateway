import json
import logging

from fastapi.testclient import TestClient

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


def test_audit_log_records_decisions_without_secrets(monkeypatch, caplog) -> None:
    """
    RME

    Requires:
        - Gateway authentication and model policy are configured.

    Modifies:
        - Temporarily configures gateway environment variables and log capture.

    Effects:
        - Sends a valid request and verifies structured security audit events.
        - Verifies prompt content and bearer credentials are not logged.

    Inputs:
        - monkeypatch: pytest environment fixture.
        - caplog: pytest log-capture fixture.

    Outputs:
        - None. Assertions determine whether auditing is safe and complete.
    """
    monkeypatch.setenv("SAG_API_KEY", "sag_test_key")
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "fake-model")
    caplog.set_level(logging.INFO, logger="secure_ai_gateway.audit")

    client = TestClient(app)
    secret_prompt = "do not log this prompt value"

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sag_test_key"},
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": secret_prompt}],
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"].startswith("req_")

    events = _audit_events(caplog)
    assert any(
        event["event"] == "authentication" and event["outcome"] == "allow"
        for event in events
    )
    assert any(
        event["event"] == "model_policy" and event["outcome"] == "allow"
        for event in events
    )

    completion_event = next(
        event for event in events if event["event"] == "chat_completion"
    )
    assert completion_event["outcome"] == "success"
    assert completion_event["model"] == "fake-model"
    assert completion_event["request_id"] == response.headers["X-Request-ID"]
    assert float(completion_event["latency_ms"]) >= 0

    serialized_events = "\n".join(record.message for record in caplog.records)
    assert secret_prompt not in serialized_events
    assert "sag_test_key" not in serialized_events


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
