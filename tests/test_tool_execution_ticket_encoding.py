import pytest
from fastapi.testclient import TestClient

from app import main
from app import tool_execution_api


@pytest.mark.parametrize(
    "token",
    [
        "abc!.def",
        "abc.def$",
        "abc=.def",
        "abc.def=",
        "abc\n.def",
        "abc.def\n",
        "abc..def",
        ".abcdef",
        "abcdef.",
        "abc.def.extra",
    ],
)
def test_noncanonical_execution_ticket_is_rejected_before_decode(
    monkeypatch,
    gateway_api_key,
    token: str,
) -> None:
    """
    RME

    Requires:
        - gateway_api_key authenticates the deterministic test client.
        - token is a deliberately non-canonical execution-ticket encoding.

    Modifies:
        - Replaces the verifier with a sentinel that must never be called.

    Effects:
        - Verifies punctuation, padding, whitespace, extra segments, and empty segments
          fail at the HTTP execution-authorization boundary before decoding.

    Inputs:
        - monkeypatch: Pytest patch helper.
        - gateway_api_key: Deterministic test client credential.
        - token: Mutated execution-ticket text.

    Outputs:
        - None. Assertions determine whether canonical encoding is enforced early.
    """
    def verifier_must_not_run(*args, **kwargs):
        raise AssertionError("noncanonical execution token reached verifier")

    monkeypatch.setattr(
        tool_execution_api,
        "verify_execution_ticket",
        verifier_must_not_run,
    )
    client = TestClient(main.app)
    response = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "tool_call": {
                "id": "call_encoding_test",
                "type": "function",
                "function": {"name": "status_check", "arguments": "{}"},
                "execution_token": token,
                "execution_risk": "read",
            }
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Tool execution is not authorized."}
    assert response.headers["X-Tool-Execution-Authorization"] == "denied"


def test_oversized_execution_ticket_is_rejected_by_request_schema(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - gateway_api_key authenticates the deterministic test client.

    Modifies:
        - Replaces the verifier with a sentinel that must never be called.

    Effects:
        - Verifies the Pydantic request boundary rejects an oversized ticket before
          execution-ticket verification is reached.

    Inputs:
        - monkeypatch: Pytest patch helper.
        - gateway_api_key: Deterministic test client credential.

    Outputs:
        - None. Assertions determine whether the outer request schema fails closed.
    """
    def verifier_must_not_run(*args, **kwargs):
        raise AssertionError("oversized execution token reached verifier")

    monkeypatch.setattr(
        tool_execution_api,
        "verify_execution_ticket",
        verifier_must_not_run,
    )
    client = TestClient(main.app)
    response = client.post(
        "/v1/tool-executions/authorize",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "tool_call": {
                "id": "call_encoding_test",
                "type": "function",
                "function": {"name": "status_check", "arguments": "{}"},
                "execution_token": "a" * 4095 + ".b",
                "execution_risk": "read",
            }
        },
    )

    assert response.status_code == 422


def test_execution_ticket_shape_accepts_only_unpadded_urlsafe_two_segments() -> None:
    """
    RME

    Requires:
        - Candidate text represents only ticket encoding shape, not ticket authenticity.

    Modifies:
        - Nothing.

    Effects:
        - Locks the canonical lexical ticket format independently of HMAC verification.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether the lexical boundary is exact.
    """
    assert tool_execution_api._is_canonical_execution_token("Abc_123-XyZ.def-456_Q")
    assert not tool_execution_api._is_canonical_execution_token("Abc_123=.def")
    assert not tool_execution_api._is_canonical_execution_token("Abc_123.def$")
