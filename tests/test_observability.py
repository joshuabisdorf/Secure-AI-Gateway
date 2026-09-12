from fastapi.testclient import TestClient

from app import main
from app.audit import emit_audit_event


def _configure_chat_policy(monkeypatch) -> None:
    """
    RME

    Requires:
        - monkeypatch is a pytest fixture.

    Modifies:
        - Test-only policy environment values.

    Effects:
        - Configures a deterministic mock-model request with no tool access.

    Inputs:
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")
    monkeypatch.setenv("SAG_CLIENT_PII_POLICIES", "test-client:redact")
    monkeypatch.setenv("SAG_CLIENT_PROMPT_INJECTION_POLICIES", "test-client:audit")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:-")


def test_metrics_expose_bounded_gateway_and_provider_series(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Deterministic mock provider and in-memory policy backends are active.

    Modifies:
        - Process-local Prometheus counters/histograms and normal test request state.

    Effects:
        - Exercises one protected chat request and scrapes /metrics.
        - Verifies request/provider/security metrics are exposed without client/key/prompt values.

    Inputs:
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Deterministic test gateway credential.

    Outputs:
        - None. Assertions determine whether metric exposure is safe and useful.
    """
    _configure_chat_policy(monkeypatch)
    client = TestClient(main.app)
    prompt = "observability-secret-prompt-marker"

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    assert response.status_code == 200

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    body = metrics.text

    assert "sag_http_requests_total" in body
    assert 'route="/v1/chat/completions"' in body
    assert "sag_provider_requests_total" in body
    assert 'provider="mock"' in body
    assert "sag_security_decisions_total" in body
    assert 'event="authentication"' in body
    assert prompt not in body
    assert "test-client" not in body
    assert "testkey" not in body
    assert gateway_api_key not in body


def test_audit_metrics_use_bounded_pii_injection_and_tool_labels() -> None:
    """
    RME

    Requires:
        - Metrics are derived from already-sanitized audit metadata.

    Modifies:
        - Process-local Prometheus counters and audit log output.

    Effects:
        - Emits representative safe audit events.
        - Verifies raw-looking tool/client/request values do not become metric labels.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether security metric labels stay bounded.
    """
    emit_audit_event(
        request_id="req_metrics_test",
        event="pii_policy",
        outcome="redact",
        client_id="private-client-marker",
        pii_detected_count=2,
        pii_types="email,person_name",
    )
    emit_audit_event(
        request_id="req_metrics_test",
        event="prompt_injection",
        outcome="audit",
        prompt_injection_detected_count=1,
        prompt_injection_indicators="instruction_override",
    )
    emit_audit_event(
        request_id="req_metrics_test",
        event="tool_execution_authorization",
        outcome="allow",
        tool_requested_names="private-tool-marker",
        tool_execution_risk="read",
    )

    metrics = TestClient(main.app).get("/metrics").text
    assert 'pii_type="email"' in metrics
    assert 'pii_type="person_name"' in metrics
    assert 'indicator="instruction_override"' in metrics
    assert 'stage="execution"' in metrics
    assert 'risk="read"' in metrics
    assert "private-client-marker" not in metrics
    assert "private-tool-marker" not in metrics
    assert "req_metrics_test" not in metrics
