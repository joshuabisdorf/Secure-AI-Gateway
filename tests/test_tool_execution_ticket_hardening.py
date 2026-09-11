import json

from fastapi.testclient import TestClient

from app import main
from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChoiceMessage,
    ToolCall,
    ToolCallFunction,
)
from app.tool_execution import prepare_tool_execution_response


def _write_registry(tmp_path, monkeypatch) -> None:
    """
    RME

    Requires:
        - tmp_path and monkeypatch are pytest fixtures.

    Modifies:
        - Creates a temporary non-secret execution policy file and environment pointer.

    Effects:
        - Defines one deterministic read-only function for ticket-hardening tests.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None.
    """
    path = tmp_path / "tool-execution-hardening.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "status_check": {
                        "risk": "read",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SAG_TOOL_EXECUTION_POLICY_FILE", str(path))
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:status_check")


def test_malformed_execution_ticket_is_controlled_denial(gateway_api_key) -> None:
    """
    RME

    Requires:
        - Test gateway authentication is configured.

    Modifies:
        - Audit logging only.

    Effects:
        - Verifies malformed Base64 ticket encoding cannot escape as an internal server error.

    Inputs:
        - gateway_api_key: Deterministic test gateway credential.

    Outputs:
        - None. Assertions determine whether malformed-ticket handling fails closed.
    """
    client = TestClient(main.app)
    response = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "tool_call": {
                "id": "call_bad_ticket",
                "type": "function",
                "function": {"name": "status_check", "arguments": "{}"},
                "execution_token": "a.a",
                "execution_risk": "read",
            }
        },
    )

    assert response.status_code == 403
    assert response.headers["X-Tool-Execution-Authorization"] == "denied"
    assert response.json() == {"detail": "Tool execution is not authorized."}


def test_execution_risk_label_tampering_is_denied(
    tmp_path,
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - A valid ticket is issued for a read-classified status_check call.

    Modifies:
        - Temporary execution-policy configuration and audit logging.

    Effects:
        - Verifies a caller cannot alter gateway-returned execution_risk independently of the signed ticket.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Deterministic test gateway credential.

    Outputs:
        - None. Assertions determine whether risk-label integrity is enforced.
    """
    _write_registry(tmp_path, monkeypatch)
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": "Check status."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "status_check",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        ],
    )
    provider_response = ChatCompletionResponse(
        id="chatcmpl-risk-tamper",
        model="mock-model",
        choices=[
            ChatChoice(
                index=0,
                message=ChoiceMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_risk_tamper",
                            function=ToolCallFunction(
                                name="status_check",
                                arguments="{}",
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
    )
    prepared = prepare_tool_execution_response(
        provider_response,
        request,
        client_id="test-client",
        key_id="testkey",
        source_request_id="req_risk_tamper",
        allowed_tools=frozenset({"status_check"}),
    )
    tool_call = prepared.response.choices[0].message.tool_calls[0].model_dump(
        exclude_none=True
    )
    tool_call["execution_risk"] = "destructive"

    client = TestClient(main.app)
    response = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={"tool_call": tool_call},
    )

    assert response.status_code == 403
    assert response.headers["X-Tool-Execution-Authorization"] == "denied"
    assert response.json() == {"detail": "Tool execution is not authorized."}
