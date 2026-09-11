import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import ChatCompletionRequest, ToolCall, ToolCallFunction
from app.providers.openai import OpenAIProvider
from app.tool_execution import (
    ToolExecutionRejected,
    parse_tool_execution_registry,
    verify_execution_ticket,
)


def _registry_document() -> str:
    """
    RME

    Requires:
        - Tests need one deterministic read-only tool execution policy.

    Modifies:
        - Nothing.

    Effects:
        - Produces an authoritative status_check schema for execution tests.

    Inputs:
        - None.

    Outputs:
        - Version-1 tool execution registry JSON.
    """
    return json.dumps(
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
    )


def _write_registry(tmp_path, monkeypatch) -> None:
    """
    RME

    Requires:
        - tmp_path and monkeypatch are pytest fixtures.

    Modifies:
        - Creates a temporary execution registry and sets its process-local path.

    Effects:
        - Configures deterministic authoritative tool metadata for one test.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None.
    """
    path = tmp_path / "tool-execution.json"
    path.write_text(_registry_document(), encoding="utf-8")
    monkeypatch.setenv("SAG_TOOL_EXECUTION_POLICY_FILE", str(path))


def _configure_chat_policy(monkeypatch) -> None:
    """
    RME

    Requires:
        - monkeypatch is a pytest fixture.

    Modifies:
        - Test-only environment policy values.

    Effects:
        - Grants test-client access to mock-model and status_check.

    Inputs:
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")
    monkeypatch.setenv("SAG_CLIENT_PII_POLICIES", "test-client:redact")
    monkeypatch.setenv("SAG_CLIENT_PROMPT_INJECTION_POLICIES", "test-client:audit")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:status_check")


def _tool_definition() -> dict[str, object]:
    """
    RME

    Requires:
        - The temporary registry uses the same status_check schema.

    Modifies:
        - Nothing.

    Effects:
        - Produces the client-visible tool declaration expected to match the authoritative registry.

    Inputs:
        - None.

    Outputs:
        - OpenAI-compatible status_check function-tool definition.
    """
    return {
        "type": "function",
        "function": {
            "name": "status_check",
            "description": "Return service status.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


def _request_tool_call(client: TestClient, gateway_api_key: str) -> dict[str, object]:
    """
    RME

    Requires:
        - Gateway test policy, mock provider, execution registry, and signing key are configured.

    Modifies:
        - Test gateway rate/usage state through one chat request.

    Effects:
        - Requests a deterministic mock model tool call and verifies a ticket was issued.

    Inputs:
        - client: FastAPI test client.
        - gateway_api_key: Raw deterministic test gateway key.

    Outputs:
        - Serialized gateway-returned tool call including execution metadata.
    """
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Check status."}],
            "tools": [_tool_definition()],
            "tool_choice": {
                "type": "function",
                "function": {"name": "status_check"},
            },
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Tool-Execution-Ticket-Count"] == "1"
    tool_call = response.json()["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["execution_risk"] == "read"
    assert isinstance(tool_call["execution_token"], str)
    assert len(tool_call["execution_token"]) > 32
    return tool_call


def test_parse_tool_execution_registry_validates_risk_and_schema() -> None:
    """
    RME

    Requires:
        - Registry JSON uses the documented version-1 schema.

    Modifies:
        - Nothing.

    Effects:
        - Verifies authoritative schemas receive deterministic fingerprints and risk labels.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether parsing is correct.
    """
    registry = parse_tool_execution_registry(_registry_document())

    spec = registry.tools["status_check"]
    assert spec.risk == "read"
    assert len(spec.schema_sha256) == 64


def test_tool_call_requires_execution_authorization_and_ticket_is_one_time(
    tmp_path,
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Test client is allowed to expose status_check.

    Modifies:
        - Temporary policy file and in-memory replay store.

    Effects:
        - Verifies a provider tool call receives a short-lived execution ticket.
        - Verifies independent execution authorization succeeds once and replay returns 409.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Test client credential.

    Outputs:
        - None. Assertions determine whether complete mediation/replay protection works.
    """
    _write_registry(tmp_path, monkeypatch)
    _configure_chat_policy(monkeypatch)
    client = TestClient(main.app)
    tool_call = _request_tool_call(client, gateway_api_key)

    authorization = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={"tool_call": tool_call},
    )
    assert authorization.status_code == 200
    assert authorization.headers["X-Tool-Execution-Authorization"] == "allowed"
    assert authorization.json()["authorized"] is True
    assert authorization.json()["tool_name"] == "status_check"
    assert authorization.json()["risk"] == "read"

    replay = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={"tool_call": tool_call},
    )
    assert replay.status_code == 409
    assert replay.headers["X-Tool-Execution-Authorization"] == "denied"


def test_execution_ticket_rejects_argument_tampering(
    tmp_path,
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - A valid ticket has been issued for exact empty-object arguments.

    Modifies:
        - Temporary policy file only.

    Effects:
        - Verifies changing model arguments after ticket issuance invalidates authorization.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Test client credential.

    Outputs:
        - None. Assertions determine whether argument binding works.
    """
    _write_registry(tmp_path, monkeypatch)
    _configure_chat_policy(monkeypatch)
    client = TestClient(main.app)
    tool_call = _request_tool_call(client, gateway_api_key)
    tool_call["function"]["arguments"] = '{"unexpected":true}'

    response = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={"tool_call": tool_call},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Tool execution is not authorized."}


def test_execution_rechecks_current_client_tool_policy(
    tmp_path,
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - A ticket was issued while status_check was allowed.

    Modifies:
        - Test-only client tool policy between issue and authorization.

    Effects:
        - Verifies revocation after model output prevents stale-ticket execution.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Test client credential.

    Outputs:
        - None. Assertions determine whether current policy is re-evaluated.
    """
    _write_registry(tmp_path, monkeypatch)
    _configure_chat_policy(monkeypatch)
    client = TestClient(main.app)
    tool_call = _request_tool_call(client, gateway_api_key)
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:-")

    response = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={"tool_call": tool_call},
    )
    assert response.status_code == 403


def test_gateway_rejects_request_schema_that_differs_from_authoritative_registry(
    tmp_path,
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Client is allowed to expose status_check by name.

    Modifies:
        - Temporary tool registry and normal in-memory test usage state.

    Effects:
        - Verifies a client cannot substitute a looser argument schema under an allowed tool name.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Test client credential.

    Outputs:
        - None. Assertions determine whether schema substitution fails closed.
    """
    _write_registry(tmp_path, monkeypatch)
    _configure_chat_policy(monkeypatch)
    client = TestClient(main.app)
    bad_tool = _tool_definition()
    bad_tool["function"]["parameters"] = {"type": "object"}

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Check status."}],
            "tools": [bad_tool],
            "tool_choice": {
                "type": "function",
                "function": {"name": "status_check"},
            },
        },
    )
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Upstream provider returned an unauthorized tool call."
    }


def test_ticket_is_bound_to_authenticated_client_and_key(
    tmp_path,
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway has issued a valid status_check ticket.

    Modifies:
        - Temporary policy file only.

    Effects:
        - Verifies ticket verification rejects a different authenticated identity.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - gateway_api_key: Test client credential.

    Outputs:
        - None. Assertions determine whether identity binding works.
    """
    _write_registry(tmp_path, monkeypatch)
    _configure_chat_policy(monkeypatch)
    client = TestClient(main.app)
    serialized = _request_tool_call(client, gateway_api_key)
    tool_call = ToolCall.model_validate(serialized)

    with pytest.raises(ToolExecutionRejected) as exc_info:
        verify_execution_ticket(
            tool_call.execution_token or "",
            tool_call,
            client_id="different-client",
            key_id="different-key",
        )
    assert exc_info.value.reason == "execution_identity_mismatch"


def test_openai_provider_never_forwards_gateway_execution_ticket() -> None:
    """
    RME

    Requires:
        - A prior assistant tool call may be included in conversation history.

    Modifies:
        - Captures one deterministic in-memory provider request payload.

    Effects:
        - Verifies gateway-only execution credentials/risk metadata never leave for OpenAI-compatible providers.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether upstream serialization is credential-safe.
    """
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """
        RME

        Requires:
            - request is the deterministic provider POST.

        Modifies:
            - captured dictionary.

        Effects:
            - Records outbound JSON and returns a minimal valid provider response.

        Inputs:
            - request: MockTransport HTTP request.

        Outputs:
            - Deterministic successful HTTP response.
        """
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-safe-history",
                "object": "chat.completion",
                "model": "tool-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenAIProvider(
        api_key="test-provider-key",
        base_url="https://provider.test/v1",
        transport=httpx.MockTransport(handler),
    )
    request = ChatCompletionRequest(
        model="tool-model",
        messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "status_check", "arguments": "{}"},
                        "execution_token": "gateway-secret-ticket-value",
                        "execution_risk": "read",
                    }
                ],
            }
        ],
    )

    import asyncio

    asyncio.run(provider.chat_completion(request))
    outbound_call = captured["messages"][0]["tool_calls"][0]
    assert "execution_token" not in outbound_call
    assert "execution_risk" not in outbound_call
