import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.usage_budget import ClientUsageBudget, PostgresUsageLedger


class FakeCursor:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self.row: tuple[object, ...] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, query: str, params: tuple[object, ...]) -> None:
        normalized = " ".join(query.split())
        self.state.setdefault("queries", []).append(normalized)

        if normalized.startswith("SELECT tokens_used, cost_used_usd"):
            self.row = (
                self.state["tokens_used"],
                self.state["cost_used_usd"],
            )
            return

        if normalized.startswith("INSERT INTO gateway_daily_usage"):
            self.state["tokens_used"] += int(params[2])
            self.state["cost_used_usd"] += Decimal(params[3])
            self.row = (
                self.state["tokens_used"],
                self.state["cost_used_usd"],
            )
            return

        raise AssertionError(f"Unexpected SQL: {normalized}")

    async def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.state)


class FakeConnectionContext:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    async def __aenter__(self):
        return FakeConnection(self.state)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakePool:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def connection(self) -> FakeConnectionContext:
        return FakeConnectionContext(self.state)


async def _exercise_ledger() -> tuple[object, object, dict[str, object]]:
    state: dict[str, object] = {
        "tokens_used": 7,
        "cost_used_usd": Decimal("0.25"),
        "queries": [],
    }
    ledger = PostgresUsageLedger(
        "postgresql://test",
        clock=lambda: datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )
    ledger._pool = FakePool(state)
    ledger._is_open = True
    budget = ClientUsageBudget(
        token_limit_daily=20,
        cost_limit_daily_usd=Decimal("1.00"),
    )

    before = await ledger.check("client-a", budget)
    after = await ledger.record(
        "client-a",
        budget,
        total_tokens=5,
        cost_usd=Decimal("0.10"),
    )
    return before, after, state


def test_postgres_usage_ledger_reads_and_atomically_increments() -> None:
    """
    RME

    Requires:
        - PostgreSQL ledger accepts a deterministic fake pool.

    Modifies:
        - In-memory fake database state.

    Effects:
        - Verifies persisted totals are read before forwarding.
        - Verifies recording uses an atomic PostgreSQL upsert increment.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether durable accounting SQL is correct.
    """
    before, after, state = asyncio.run(_exercise_ledger())

    assert before.tokens_used_daily == 7
    assert before.cost_used_daily_usd == Decimal("0.25")
    assert after.tokens_used_daily == 12
    assert after.cost_used_daily_usd == Decimal("0.35")
    assert after.tokens_remaining_daily == 8
    assert after.cost_remaining_daily_usd == Decimal("0.65")

    queries = "\n".join(state["queries"])
    assert "ON CONFLICT (client_id, usage_date)" in queries
    assert "gateway_daily_usage.tokens_used + EXCLUDED.tokens_used" in queries
    assert "gateway_daily_usage.cost_used_usd + EXCLUDED.cost_used_usd" in queries
