import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import string

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.api_keys import parse_key_id
from app.audit import audit_logger
from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChoiceMessage,
    ToolCall,
    ToolCallFunction,
)
from app.providers.base import ProviderError
from app.providers.openai import OpenAIProvider
from app.security_policy import (
    SecurityPolicyUnavailable,
    clear_security_policy_cache,
    load_security_policy_registry,
)
from app.server import RequestBodyLimitMiddleware
from app.tool_execution import (
    ToolExecutionRejected,
    prepare_tool_execution_response,
    verify_execution_ticket,
)


def _write_tool_registry(
    tmp_path,
    monkeypatch,
    *,
    risk: str = "read",
    parameters: dict[str, object] | None = None,
):
    """
    RME

    Requires:
        - tmp_path and monkeypatch are pytest fixtures.
        - risk and parameters describe a valid execution-time tool policy.

    Modifies:
        - Creates a temporary execution-policy file.
        - Temporarily points the process at that file and grants the test tool.

    Effects:
        - Installs one deterministic status_check tool for adversarial ticket
        - tests.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - risk: Tool risk classification.
        - parameters: Optional authoritative JSON Schema.

    Outputs:
        - Path to the temporary execution-policy file.
    """
    schema = parameters or {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    path = tmp_path / "m10-tool-execution.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "status_check": {
                        "risk": risk,
                        "parameters": schema,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SAG_TOOL_EXECUTION_POLICY_FILE", str(path))
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "test-client:status_check")
    return path


def _issue_tool_call(
    tmp_path,
    monkeypatch,
    *,
    arguments: str = "{}",
    parameters: dict[str, object] | None = None,
) -> ToolCall:
    """
    RME

    Requires:
        - A valid test signing key is configured by tests/conftest.py.
        - arguments are intended for the supplied authoritative schema.

    Modifies:
        - Temporary execution-policy configuration.

    Effects:
        - Produces a real gateway-signed execution ticket without invoking an
        - external provider.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - arguments: Model-produced function arguments.
        - parameters: Optional authoritative JSON Schema.

    Outputs:
        - ToolCall containing a signed execution token and risk label.
    """
    schema = parameters or {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    _write_tool_registry(
        tmp_path,
        monkeypatch,
        parameters=schema,
    )
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": "Check status."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "status_check",
                    "parameters": schema,
                },
            }
        ],
    )
    provider_response = ChatCompletionResponse(
        id="chatcmpl-m10-ticket",
        model="mock-model",
        choices=[
            ChatChoice(
                index=0,
                message=ChoiceMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_m10_ticket",
                            function=ToolCallFunction(
                                name="status_check",
                                arguments=arguments,
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
        source_request_id="req_m10_ticket",
        allowed_tools=frozenset({"status_check"}),
    )
    return prepared.response.choices[0].message.tool_calls[0]


def _mutate_and_resign_ticket(token: str, mutation) -> str:
    """
    RME

    Requires:
        - token is a gateway-issued two-segment execution ticket.
        - mutation changes the decoded ticket payload dictionary.
        - The deterministic test signing key is configured.

    Modifies:
        - Nothing outside local test data.

    Effects:
        - Re-signs a deliberately malformed semantic payload so verifier
        - structure checks are exercised after HMAC verification.

    Inputs:
        - token: Original signed execution ticket.
        - mutation: Callable that mutates a decoded payload dictionary.

    Outputs:
        - Newly signed malformed execution ticket.
    """
    payload_segment, _ = token.split(".", 1)
    padding = "=" * (-len(payload_segment) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode((payload_segment + padding).encode("ascii"))
    )
    mutation(payload)
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    key = os.environ["SAG_TOOL_EXECUTION_SIGNING_KEY"].encode("utf-8")
    signature = hmac.new(key, payload_bytes, hashlib.sha256).digest()
    encoded_payload = (
        base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    )
    encoded_signature = (
        base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    return f"{encoded_payload}.{encoded_signature}"


def test_malformed_http_json_corpus_fails_closed(gateway_api_key) -> None:
    """
    RME

    Requires:
        - The deterministic gateway test client is configured.

    Modifies:
        - Process-local request/audit state only.

    Effects:
        - Verifies malformed JSON and non-object bodies are controlled
        - validation failures.

    Inputs:
        - gateway_api_key: Deterministic test gateway credential.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    client = TestClient(main.app)
    headers = {
        "Authorization": f"Bearer {gateway_api_key}",
        "Content-Type": "application/json",
    }
    malformed = (
        "{",
        '{"model":"mock-model","messages":[}',
        '"just-a-string"',
        "[]",
        "null",
    )

    for body in malformed:
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            content=body,
        )
        assert 400 <= response.status_code < 500
        assert gateway_api_key not in response.text


@pytest.mark.parametrize(
    "content_lengths",
    [
        [b"1", b"2"],
        [b"not-a-number"],
        [b"-1"],
    ],
)
def test_request_body_framing_corpus_is_rejected(content_lengths) -> None:
    """
    RME

    Requires:
        - content_lengths contains deliberately invalid Content-Length values.

    Modifies:
        - Captured ASGI response messages only.

    Effects:
        - Verifies ambiguous, non-numeric, and negative framing is rejected
        - before application parsing.

    Inputs:
        - content_lengths: Candidate Content-Length header values.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    sent: list[dict[str, object]] = []
    inner_called = False

    async def inner(scope, receive, send) -> None:
        nonlocal inner_called
        inner_called = True

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(inner, max_body_bytes=1024)
    scope = {
        "type": "http",
        "path": "/v1/chat/completions",
        "headers": [(b"content-length", value) for value in content_lengths],
    }
    asyncio.run(middleware(scope, receive, send))

    assert inner_called is False
    assert sent[0]["status"] == 413


def test_api_key_parser_negative_and_deterministic_fuzz_corpus() -> None:
    """
    RME

    Requires:
        - parse_key_id accepts only the documented structured bearer-key format.

    Modifies:
        - Deterministic local pseudo-random generator state only.

    Effects:
        - Exercises fixed boundary cases and 256 deterministic invalid fuzz
        - strings.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    invalid = (
        "",
        "sag",
        "sag_",
        "sag_a_secret",
        "sag_abc_secret",
        "sag_ab!d_secret",
        "sag_abcd_",
        "SAG_abcd_secret",
        "sag_" + "a" * 33 + "_secret",
        "bearer_sag_abcd_secret",
    )
    for candidate in invalid:
        assert parse_key_id(candidate) is None

    rng = random.Random(20260914)
    alphabet = string.ascii_letters + string.digits + string.punctuation + " "
    for _ in range(256):
        candidate = "x" + "".join(
            rng.choice(alphabet) for _ in range(rng.randint(0, 96))
        )
        assert parse_key_id(candidate) is None

    assert parse_key_id("sag_abcd_nonempty-secret") == "abcd"
    assert parse_key_id("sag_A1-b_secret_with_underscores") == "A1-b"


def test_execution_ticket_encoding_mutation_corpus_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    """
    RME

    Requires:
        - A valid gateway execution ticket can be issued locally.

    Modifies:
        - Temporary execution-policy configuration.

    Effects:
        - Verifies signature, segmentation, and size mutations fail closed.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    tool_call = _issue_tool_call(tmp_path, monkeypatch)
    token = tool_call.execution_token
    assert token is not None
    payload, signature = token.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    mutations = (
        token + ".extra",
        f"{payload}.{replacement}{signature[1:]}",
        f"{signature}.{payload}",
        f".{signature}",
        f"{payload}.",
        "a.a",
        "x" * 4097,
    )

    for mutated in mutations:
        with pytest.raises(ToolExecutionRejected):
            verify_execution_ticket(
                mutated,
                tool_call,
                client_id="test-client",
                key_id="testkey",
            )


def test_execution_ticket_semantic_mutation_corpus_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    """
    RME

    Requires:
        - The deterministic test signing key may be used to exercise post-HMAC
        - validation paths.

    Modifies:
        - Temporary execution-policy configuration.

    Effects:
        - Verifies structurally invalid but correctly signed ticket payloads are
        - rejected.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    tool_call = _issue_tool_call(tmp_path, monkeypatch)
    token = tool_call.execution_token
    assert token is not None

    mutations = (
        lambda payload: payload.update({"unexpected": "field"}),
        lambda payload: payload.update({"v": 2}),
        lambda payload: payload.update({"exp": "tomorrow"}),
        lambda payload: payload.update({"execution_id": "not-hex"}),
        lambda payload: payload.update({"client_id": 7}),
    )
    for mutation in mutations:
        malformed = _mutate_and_resign_ticket(token, mutation)
        with pytest.raises(ToolExecutionRejected):
            verify_execution_ticket(
                malformed,
                tool_call,
                client_id="test-client",
                key_id="testkey",
            )


@pytest.mark.parametrize(
    "arguments",
    [
        '{"count":0,"mode":"safe","tags":[]}',
        '{"count":4,"mode":"safe","tags":[]}',
        '{"count":2,"mode":"other","tags":[]}',
        '{"count":2,"mode":"safe","tags":["a","b","c"]}',
        '{"count":2,"mode":"safe","tags":[],"extra":true}',
        '{"count":"2","mode":"safe","tags":[]}',
    ],
)
def test_tool_argument_json_schema_edge_corpus_is_rejected(
    tmp_path,
    monkeypatch,
    arguments,
) -> None:
    """
    RME

    Requires:
        - The authoritative schema contains numeric, enum, array, type, and
        - property constraints.

    Modifies:
        - Temporary execution-policy configuration.

    Effects:
        - Verifies edge-case model arguments cannot bypass execution-time JSON
        - Schema enforcement.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - arguments: Deliberately invalid model argument JSON.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "mode": {"type": "string", "enum": ["safe", "dry-run"]},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 2,
            },
        },
        "required": ["count", "mode", "tags"],
        "additionalProperties": False,
    }
    with pytest.raises(ToolExecutionRejected, match="tool_arguments_invalid"):
        _issue_tool_call(
            tmp_path,
            monkeypatch,
            arguments=arguments,
            parameters=schema,
        )


def test_provider_malformed_response_corpus_fails_closed() -> None:
    """
    RME

    Requires:
        - OpenAI-compatible transport accepts an injected deterministic
        - MockTransport.

    Modifies:
        - In-memory HTTP transport state only.

    Effects:
        - Verifies malformed successful upstream responses become non-secret
        - ProviderError failures.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    malformed_payloads: list[object] = [
        None,
        {},
        {"id": "x", "model": "m", "choices": "not-a-list"},
        {
            "id": "x",
            "model": "m",
            "choices": [],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": -1,
            },
        },
        {
            "id": "x",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "x" * 131_073},
                    "finish_reason": "stop",
                }
            ],
        },
    ]
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": "hello"}],
    )

    async def exercise(payload: object) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            if payload is None:
                return httpx.Response(200, content=b"not-json")
            return httpx.Response(200, json=payload)

        provider = OpenAIProvider(
            api_key="test-key",
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderError, match="invalid_upstream_response"):
            await provider.chat_completion(request)

    for payload in malformed_payloads:
        asyncio.run(exercise(payload))


def test_provider_timeout_is_bounded_provider_failure() -> None:
    """
    RME

    Requires:
        - MockTransport can raise an httpx timeout without network access.

    Modifies:
        - In-memory HTTP transport state only.

    Effects:
        - Verifies upstream timeout is normalized to the controlled
        - upstream_timeout reason.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": "hello"}],
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("deterministic timeout", request=request)

    provider = OpenAIProvider(
        api_key="test-key",
        base_url="https://provider.invalid/v1",
        timeout_seconds=0.01,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError, match="upstream_timeout"):
        asyncio.run(provider.chat_completion(request))


def test_tool_execution_audit_never_logs_ticket_or_arguments(
    tmp_path,
    monkeypatch,
    caplog,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - A valid execution ticket is issued for an argument containing a
        - sentinel secret.

    Modifies:
        - Temporary execution policy, replay state, and captured audit logging.

    Effects:
        - Verifies execution authorization logs safe metadata but never raw
        - arguments or ticket text.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.
        - caplog: Pytest log-capture fixture.
        - gateway_api_key: Deterministic gateway credential.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    sentinel = "M10_ARGUMENT_SECRET_7f4d3f"
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    tool_call = _issue_tool_call(
        tmp_path,
        monkeypatch,
        arguments=json.dumps({"query": sentinel}),
        parameters=schema,
    )
    assert tool_call.execution_token is not None
    caplog.set_level(logging.INFO, logger="secure_ai_gateway.audit")
    client = TestClient(main.app)

    audit_logger.addHandler(caplog.handler)
    try:
        response = client.post(
            "/v1/tool-executions/authorize",
            headers={"Authorization": f"Bearer {gateway_api_key}"},
            json={"tool_call": tool_call.model_dump(exclude_none=True)},
        )
    finally:
        audit_logger.removeHandler(caplog.handler)

    assert response.status_code == 200
    serialized = "\n".join(record.message for record in caplog.records)
    assert sentinel not in serialized
    assert tool_call.execution_token not in serialized
    assert gateway_api_key not in serialized
    assert "status_check" in serialized


def test_security_policy_cache_reloads_and_invalid_update_fails_closed(
    tmp_path,
) -> None:
    """
    RME

    Requires:
        - A writable temporary policy file is available.

    Modifies:
        - Process-local policy cache and the temporary policy file.

    Effects:
        - Verifies changed policy is reloaded and an invalid replacement never
        - falls back to stale grants.

    Inputs:
        - tmp_path: Pytest temporary directory.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    clear_security_policy_cache()
    path = tmp_path / "security-policy.json"

    def document(rate: int) -> str:
        return json.dumps(
            {
                "version": 1,
                "profiles": {
                    "default": {
                        "allowed_models": ["mock-model"],
                        "requests_per_minute": rate,
                        "daily_budget": {"tokens": 1000, "cost_usd": "10.00"},
                        "pii_action": "redact",
                        "prompt_injection_action": "audit",
                        "allowed_tools": [],
                    }
                },
                "clients": {"test-client": "default"},
            }
        )

    path.write_text(document(10), encoding="utf-8")
    first = load_security_policy_registry(str(path))
    assert first.resolve("test-client").profile.requests_per_minute == 10

    path.write_text(document(11), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = load_security_policy_registry(str(path))
    assert second.resolve("test-client").profile.requests_per_minute == 11

    path.write_text('{"version":1}', encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    with pytest.raises(
        SecurityPolicyUnavailable, match="invalid_security_policy_file"
    ):
        load_security_policy_registry(str(path))


def test_execution_ticket_is_invalidated_by_policy_change(
    tmp_path,
    monkeypatch,
) -> None:
    """
    RME

    Requires:
        - A valid ticket is issued while the authoritative tool risk is read.

    Modifies:
        - Temporary execution-policy file and execution registry cache.

    Effects:
        - Verifies a stale ticket is rejected after authoritative execution
        - policy changes.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest environment patch helper.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    tool_call = _issue_tool_call(tmp_path, monkeypatch)
    token = tool_call.execution_token
    assert token is not None
    policy_path = tmp_path / "m10-tool-execution.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["tools"]["status_check"]["risk"] = "write"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    stat = policy_path.stat()
    os.utime(
        policy_path,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
    )

    with pytest.raises(
        ToolExecutionRejected, match="tool_execution_policy_changed"
    ):
        verify_execution_ticket(
            token,
            tool_call,
            client_id="test-client",
            key_id="testkey",
        )
