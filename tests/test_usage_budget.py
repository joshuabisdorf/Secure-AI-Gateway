import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.usage_budget import (
    ClientUsageBudget,
    InMemoryUsageLedger,
    parse_client_usage_budgets,
)


def test_parse_client_usage_budgets_supports_optional_dimensions() -> None:
    """
    RME

    Requires:
        - Usage-budget configuration uses the documented record format.

    Modifies:
        - Nothing.

    Effects:
        - Verifies combined, token-only, and cost-only budgets are parsed.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether parsing works correctly.
    """
    budgets = parse_client_usage_budgets(
        "client-a:1000:2.50,client-b:5000:-,client-c:-:1.25"
    )

    assert budgets["client-a"].token_limit_daily == 1000
    assert budgets["client-a"].cost_limit_daily_usd == Decimal("2.50")
    assert budgets["client-b"].token_limit_daily == 5000
    assert budgets["client-b"].cost_limit_daily_usd is None
    assert budgets["client-c"].token_limit_daily is None
    assert budgets["client-c"].cost_limit_daily_usd == Decimal("1.25")


def test_usage_ledger_enforces_and_resets_daily_budget() -> None:
    """
    RME

    Requires:
        - The in-memory usage ledger accepts an injectable UTC clock.

    Modifies:
        - Process-local usage state inside the test ledger.

    Effects:
        - Records token and cost usage through the async ledger interface.
        - Verifies exhausted budgets block subsequent requests.
        - Verifies a new UTC day resets usage.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether daily accounting works correctly.
    """
    now = [datetime(2026, 9, 9, 23, 59, tzinfo=timezone.utc)]
    ledger = InMemoryUsageLedger(clock=lambda: now[0])
    budget = ClientUsageBudget(
        token_limit_daily=10,
        cost_limit_daily_usd=Decimal("1.00"),
    )

    initial = asyncio.run(ledger.check("client-a", budget))
    assert initial.allowed is True
    assert initial.tokens_remaining_daily == 10

    recorded = asyncio.run(
        ledger.record(
            "client-a",
            budget,
            total_tokens=10,
            cost_usd=Decimal("0.25"),
        )
    )
    assert recorded.allowed is False
    assert recorded.reason == "token_budget_exceeded"
    assert recorded.tokens_used_daily == 10

    denied = asyncio.run(ledger.check("client-a", budget))
    assert denied.allowed is False

    now[0] = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
    reset = asyncio.run(ledger.check("client-a", budget))
    assert reset.allowed is True
    assert reset.tokens_used_daily == 0
    assert reset.cost_used_daily_usd == Decimal("0")


def test_chat_blocks_request_after_daily_token_budget_is_consumed(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - The mock provider reports deterministic usage of five total tokens.
        - Gateway authentication, rate limiting, and model policy are configured.

    Modifies:
        - Temporarily sets a five-token daily budget for the test client.
        - In-memory test usage totals through gateway requests.

    Effects:
        - Verifies the request that reaches the budget is returned successfully.
        - Verifies the next request is blocked before provider forwarding.

    Inputs:
        - monkeypatch: pytest environment fixture.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether gateway budget enforcement works.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")
    monkeypatch.setenv("SAG_CLIENT_DAILY_BUDGETS", "test-client:5:1.00")

    client = TestClient(app)
    request = {
        "model": "mock-model",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    headers = {"Authorization": f"Bearer {gateway_api_key}"}

    first = client.post("/v1/chat/completions", headers=headers, json=request)
    assert first.status_code == 200
    assert first.json()["usage"]["total_tokens"] == 5
    assert first.headers["X-Usage-Tokens-Used"] == "5"
    assert first.headers["X-Usage-Tokens-Remaining"] == "0"

    second = client.post("/v1/chat/completions", headers=headers, json=request)
    assert second.status_code == 403
    assert second.json() == {"detail": "Usage budget exceeded."}
    assert "X-Usage-Budget-Reset" in second.headers


def test_chat_fails_closed_without_usage_budget_policy(
    monkeypatch,
    gateway_api_key,
) -> None:
    """
    RME

    Requires:
        - Gateway authentication, rate limiting, and model policy are configured.

    Modifies:
        - Temporarily removes SAG_CLIENT_DAILY_BUDGETS.

    Effects:
        - Verifies a protected request is not forwarded without usage-budget policy.

    Inputs:
        - monkeypatch: pytest fixture used to modify the environment.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether budget policy fails closed.
    """
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")
    monkeypatch.delenv("SAG_CLIENT_DAILY_BUDGETS", raising=False)

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_api_key}"},
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Usage budget policy is not configured."}
