import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rate_limit import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    parse_client_rate_limits,
)


def test_parse_client_rate_limits() -> None:
    """
    RME

    Requires:
        - Per-client rate-limit records use client_id:requests_per_minute format.

    Modifies:
        - Nothing.

    Effects:
        - Verifies valid rate-limit configuration is parsed by client identity.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether parsing works correctly.
    """
    limits = parse_client_rate_limits("client-a:10,client-b:25")
    assert limits == {"client-a": 10, "client-b": 25}


@pytest.mark.parametrize(
    "configured_limits",
    [
        "",
        "client-a",
        "client-a:0",
        "client-a:not-a-number",
        "client-a:10,client-a:20",
    ],
)
def test_parse_client_rate_limits_rejects_invalid_configuration(
    configured_limits: str,
) -> None:
    """Verify malformed or unsafe rate-limit configuration is rejected."""
    with pytest.raises(ValueError):
        parse_client_rate_limits(configured_limits)


def test_in_memory_rate_limiter_resets_after_window() -> None:
    """
    RME

    Requires:
        - The limiter can use an injected monotonic clock.

    Modifies:
        - In-memory limiter state and the test clock value.

    Effects:
        - Verifies a fixed window allows up to the configured limit.
        - Verifies excess requests are denied until the next window.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether fixed-window behavior is correct.
    """
    now = [100.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])

    first = asyncio.run(limiter.check("client-a", 2))
    second = asyncio.run(limiter.check("client-a", 2))
    denied = asyncio.run(limiter.check("client-a", 2))

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert denied.allowed is False
    assert denied.retry_after_seconds == 60

    now[0] += 60.0
    reset = asyncio.run(limiter.check("client-a", 2))
    assert reset.allowed is True
    assert reset.remaining == 1


class _FakeRedis:
    def __init__(self) -> None:
        self.results = [(1, 1), (2, 1), (2, 0)]
        self.commands: list[tuple[object, ...]] = []
        self.closed = False

    async def execute_command(self, *args: object) -> object:
        self.commands.append(args)
        return self.results.pop(0)

    async def ttl(self, name: str) -> int:
        assert name == "sag:rate_limit:client-a"
        return 41

    async def aclose(self) -> None:
        self.closed = True


def test_redis_rate_limiter_uses_atomic_increx_window() -> None:
    """
    RME

    Requires:
        - RedisRateLimiter accepts a Redis-compatible deterministic test client.

    Modifies:
        - Fake Redis command history and queued responses.

    Effects:
        - Verifies allowed and denied distributed rate-limit decisions.
        - Verifies INCREX uses a bound and expiration without extending the window.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether Redis enforcement is configured correctly.
    """
    fake = _FakeRedis()
    limiter = RedisRateLimiter("redis://unused", client=fake)

    first = asyncio.run(limiter.check("client-a", 2))
    second = asyncio.run(limiter.check("client-a", 2))
    denied = asyncio.run(limiter.check("client-a", 2))

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert denied.allowed is False
    assert denied.retry_after_seconds == 41
    assert fake.commands[0] == (
        "INCREX",
        "sag:rate_limit:client-a",
        "BYINT",
        1,
        "UBOUND",
        2,
        "EX",
        60,
        "ENX",
    )

    asyncio.run(limiter.close())
    assert fake.closed is True


def test_chat_rate_limit_returns_429(monkeypatch, gateway_api_key) -> None:
    """
    RME

    Requires:
        - Gateway client authentication and model policy are configured.
        - Tests use the deterministic in-memory rate limiter.

    Modifies:
        - Temporarily configures a two-request-per-minute client limit.
        - Process-local test rate-limit state.

    Effects:
        - Verifies allowed responses expose remaining quota.
        - Verifies the next request returns 429 with Retry-After.

    Inputs:
        - monkeypatch: pytest fixture used to configure test policy.
        - gateway_api_key: Raw API key for the configured test client.

    Outputs:
        - None. Assertions determine whether endpoint throttling works correctly.
    """
    monkeypatch.setenv("SAG_CLIENT_RATE_LIMITS", "test-client:2")
    monkeypatch.setenv("SAG_ALLOWED_MODELS", "mock-model")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "test-client:mock-model")

    client = TestClient(app)
    request_kwargs = {
        "headers": {"Authorization": f"Bearer {gateway_api_key}"},
        "json": {
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    }

    first = client.post("/v1/chat/completions", **request_kwargs)
    second = client.post("/v1/chat/completions", **request_kwargs)
    denied = client.post("/v1/chat/completions", **request_kwargs)

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"
    assert second.status_code == 200
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert denied.status_code == 429
    assert denied.json() == {"detail": "Rate limit exceeded."}
    assert denied.headers["X-RateLimit-Limit"] == "2"
    assert denied.headers["X-RateLimit-Remaining"] == "0"
    assert int(denied.headers["Retry-After"]) >= 1


def test_chat_fails_closed_without_client_rate_policy(
    monkeypatch,
    gateway_api_key,
) -> None:
    """Verify authenticated requests fail closed without rate-limit policy."""
    monkeypatch.delenv("SAG_CLIENT_RATE_LIMITS", raising=False)

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
    assert response.json() == {"detail": "Rate limit policy is not configured."}
