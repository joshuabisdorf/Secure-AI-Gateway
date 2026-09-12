import asyncio

import app.readiness as readiness


def test_readiness_skips_unconfigured_shared_backends(monkeypatch) -> None:
    """
    RME

    Requires:
        - pytest monkeypatch can configure deterministic non-network backends.

    Modifies:
        - Runtime backend-selection environment for this test.

    Effects:
        - Verifies in-memory/environment test backends do not trigger network readiness checks.

    Inputs:
        - monkeypatch: pytest environment-patching fixture.

    Outputs:
        - None. Assertions determine readiness behavior.
    """
    monkeypatch.setenv("SAG_CLIENT_REGISTRY_BACKEND", "environment")
    monkeypatch.setenv("SAG_USAGE_LEDGER_BACKEND", "memory")
    monkeypatch.setenv("SAG_RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "memory")

    ready, unavailable = asyncio.run(readiness.runtime_ready())

    assert ready is True
    assert unavailable == ()


def test_readiness_fails_closed_when_postgres_config_is_missing(monkeypatch) -> None:
    """
    RME

    Requires:
        - PostgreSQL can be selected without a DATABASE_URL for the test.

    Modifies:
        - Runtime backend-selection environment for this test.

    Effects:
        - Verifies a required but unconfigured PostgreSQL backend makes the replica not ready.

    Inputs:
        - monkeypatch: pytest environment-patching fixture.

    Outputs:
        - None. Assertions determine readiness behavior.
    """
    monkeypatch.setenv("SAG_CLIENT_REGISTRY_BACKEND", "postgres")
    monkeypatch.setenv("SAG_USAGE_LEDGER_BACKEND", "memory")
    monkeypatch.setenv("SAG_RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "memory")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    ready, unavailable = asyncio.run(readiness.runtime_ready())

    assert ready is False
    assert unavailable == ("postgres",)


def test_readiness_checks_required_postgres_and_redis(monkeypatch) -> None:
    """
    RME

    Requires:
        - Readiness backend probes can be replaced with deterministic async stubs.

    Modifies:
        - Runtime backend-selection environment and probe functions for this test.

    Effects:
        - Verifies required shared backends are both checked and failures use safe component names.

    Inputs:
        - monkeypatch: pytest patching fixture.

    Outputs:
        - None. Assertions determine readiness behavior.
    """
    monkeypatch.setenv("SAG_CLIENT_REGISTRY_BACKEND", "postgres")
    monkeypatch.setenv("SAG_USAGE_LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("SAG_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "redis")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid/0")

    async def postgres_ready(_: str) -> bool:
        return True

    async def redis_ready(_: str) -> bool:
        return False

    monkeypatch.setattr(readiness, "_postgres_ready", postgres_ready)
    monkeypatch.setattr(readiness, "_redis_ready", redis_ready)

    ready, unavailable = asyncio.run(readiness.runtime_ready())

    assert ready is False
    assert unavailable == ("redis",)
