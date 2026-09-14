import asyncio
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from redis.asyncio import Redis

from app import database
from app.api_keys import parse_key_id
from app.clients import (
    create_client_key,
    list_client_keys,
    revoke_client_key,
    rotate_client_keys,
)
from app.database import migrate_database
from app.rate_limit import RedisRateLimiter
from app.redis_replay import SharedRedisToolExecutionReplayStore
from app.usage_budget import (
    ClientUsageBudget,
    PostgresUsageLedger,
    UsageLedgerUnavailable,
)

pytestmark = pytest.mark.skipif(
    os.getenv("SAG_RUN_M10_INTEGRATION") != "1",
    reason="requires dedicated Redis and PostgreSQL integration services",
)


def _database_url() -> str:
    """
    RME

    Requires:
        - DATABASE_URL identifies the dedicated M10 PostgreSQL service.

    Modifies:
        - Nothing.

    Effects:
        - Fails immediately when integration database configuration is absent.

    Inputs:
        - None.

    Outputs:
        - Configured PostgreSQL connection string.
    """
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required for M10 integration tests")
    return value


def _redis_url() -> str:
    """
    RME

    Requires:
        - REDIS_URL identifies the dedicated M10 Redis/Valkey service.

    Modifies:
        - Nothing.

    Effects:
        - Fails immediately when integration Redis configuration is absent.

    Inputs:
        - None.

    Outputs:
        - Configured Redis-compatible connection URL.
    """
    value = os.getenv("REDIS_URL", "").strip()
    if not value:
        raise RuntimeError("REDIS_URL is required for M10 integration tests")
    return value


async def _initialize_database() -> None:
    """
    RME

    Requires:
        - The dedicated M10 PostgreSQL service is reachable.

    Modifies:
        - Applies idempotent gateway schema migrations.

    Effects:
        - Ensures client and usage tables exist before concurrency tests run.

    Inputs:
        - None.

    Outputs:
        - None.
    """
    await migrate_database(_database_url())


def test_real_redis_execution_ticket_claim_race_has_one_winner() -> None:
    """
    RME

    Requires:
        - Dedicated Redis/Valkey integration service is available.

    Modifies:
        - One temporary shared replay key in Redis.

    Effects:
        - Races 24 independent replay-store clients against the same execution ID.
        - Verifies SET NX permits exactly one successful distributed claim.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """

    async def exercise() -> None:
        redis_url = _redis_url()
        execution_id = uuid4().hex
        redis = Redis.from_url(redis_url, decode_responses=True)
        stores = [SharedRedisToolExecutionReplayStore(redis_url) for _ in range(24)]
        try:
            await redis.delete(f"sag:tool_execution:{execution_id}")
            results = await asyncio.gather(
                *(store.claim(execution_id, 30) for store in stores)
            )
            assert sum(bool(result) for result in results) == 1
        finally:
            await asyncio.gather(*(store.close() for store in stores))
            await redis.aclose()

    asyncio.run(exercise())


def test_real_redis_rate_limit_contention_never_exceeds_limit() -> None:
    """
    RME

    Requires:
        - Dedicated Redis/Valkey integration service supports the gateway Lua script.

    Modifies:
        - One temporary shared rate-limit bucket in Redis.

    Effects:
        - Races 80 checks across four independent limiter instances.
        - Verifies exactly the configured capacity is granted under contention.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """

    async def exercise() -> None:
        redis_url = _redis_url()
        client_id = f"m10-rate-{uuid4().hex[:12]}"
        limit = 19
        redis = Redis.from_url(redis_url, decode_responses=True)
        limiters = [RedisRateLimiter(redis_url, window_seconds=30) for _ in range(4)]
        try:
            await redis.delete(f"sag:rate_limit:{client_id}")
            decisions = await asyncio.gather(
                *(
                    limiters[index % len(limiters)].check(client_id, limit)
                    for index in range(80)
                )
            )
            allowed = [decision for decision in decisions if decision.allowed]
            denied = [decision for decision in decisions if not decision.allowed]
            assert len(allowed) == limit
            assert len(denied) == 80 - limit
            assert all(decision.remaining >= 0 for decision in allowed)
            assert all(
                decision.retry_after_seconds is not None
                and decision.retry_after_seconds >= 1
                for decision in denied
            )
        finally:
            await asyncio.gather(*(limiter.close() for limiter in limiters))
            await redis.aclose()

    asyncio.run(exercise())


def test_postgres_usage_ledger_concurrent_updates_are_lossless() -> None:
    """
    RME

    Requires:
        - Dedicated PostgreSQL integration service is available.

    Modifies:
        - Creates one temporary gateway client/key and its daily usage row.

    Effects:
        - Records 48 concurrent usage updates through the real PostgreSQL upsert path.
        - Verifies no token or cost increments are lost.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """

    async def exercise() -> None:
        await _initialize_database()
        database_url = _database_url()
        client_id = f"m10-usage-{uuid4().hex[:12]}"
        await create_client_key(database_url, client_id)
        ledger = PostgresUsageLedger(
            database_url,
            min_size=1,
            max_size=8,
            clock=lambda: datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc),
        )
        budget = ClientUsageBudget(
            token_limit_daily=1_000_000,
            cost_limit_daily_usd=Decimal("1000.00"),
        )
        try:
            await asyncio.gather(
                *(
                    ledger.record(
                        client_id,
                        budget,
                        total_tokens=3,
                        cost_usd=Decimal("0.01"),
                    )
                    for _ in range(48)
                )
            )
            final = await ledger.check(client_id, budget)
            assert final.tokens_used_daily == 144
            assert final.cost_used_daily_usd == Decimal("0.48")
        finally:
            await ledger.close()

    asyncio.run(exercise())


def test_concurrent_key_rotations_leave_exactly_one_active_key() -> None:
    """
    RME

    Requires:
        - Dedicated PostgreSQL integration service is available.

    Modifies:
        - Creates and repeatedly rotates one temporary gateway client identity.

    Effects:
        - Races six independent rotation transactions.
        - Verifies client-row serialization leaves exactly one active key.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """

    async def exercise() -> None:
        await _initialize_database()
        database_url = _database_url()
        client_id = f"m10-rotate-{uuid4().hex[:12]}"
        await create_client_key(database_url, client_id)

        results = await asyncio.gather(
            *(rotate_client_keys(database_url, client_id) for _ in range(6))
        )
        assert len({key_id for _, key_id, _ in results}) == 6

        rows = [row for row in await list_client_keys(database_url) if row[0] == client_id]
        active_rows = [row for row in rows if row[2] and row[3]]
        assert len(active_rows) == 1

    asyncio.run(exercise())


def test_rotation_and_revocation_race_never_creates_multiple_active_keys() -> None:
    """
    RME

    Requires:
        - Dedicated PostgreSQL integration service is available.

    Modifies:
        - Creates one temporary client and races administrative key operations.

    Effects:
        - Verifies concurrent explicit revocation and rotation cannot produce multiple active credentials.
        - Allows zero active credentials when explicit revocation wins, which is fail-closed behavior.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """

    async def exercise() -> None:
        await _initialize_database()
        database_url = _database_url()
        client_id = f"m10-revoke-{uuid4().hex[:12]}"
        raw_key = await create_client_key(database_url, client_id)
        original_key_id = parse_key_id(raw_key)
        assert original_key_id is not None

        outcomes = await asyncio.gather(
            rotate_client_keys(database_url, client_id),
            revoke_client_key(database_url, original_key_id),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                assert isinstance(outcome, ValueError)
                assert str(outcome) in {"no_active_keys_to_rotate", "unknown_key_id"}

        rows = [row for row in await list_client_keys(database_url) if row[0] == client_id]
        active_rows = [row for row in rows if row[2] and row[3]]
        assert len(active_rows) <= 1

    asyncio.run(exercise())


def test_usage_pool_exhaustion_is_bounded_and_fails_closed() -> None:
    """
    RME

    Requires:
        - Dedicated PostgreSQL integration service is available.
        - PostgresUsageLedger enforces a short configured pool wait timeout.

    Modifies:
        - Creates one temporary gateway client and opens a one-connection pool.

    Effects:
        - Holds the sole connection and verifies a second ledger operation fails closed within a bounded interval.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine pass/fail.
    """

    async def exercise() -> None:
        await _initialize_database()
        database_url = _database_url()
        client_id = f"m10-pool-{uuid4().hex[:12]}"
        await create_client_key(database_url, client_id)
        ledger = PostgresUsageLedger(
            database_url,
            min_size=1,
            max_size=1,
            connection_timeout_seconds=0.25,
        )
        budget = ClientUsageBudget(
            token_limit_daily=100,
            cost_limit_daily_usd=None,
        )
        await ledger._ensure_open()
        started = time.monotonic()
        try:
            async with ledger._pool.connection():
                with pytest.raises(UsageLedgerUnavailable, match="usage_ledger_unavailable"):
                    await ledger.check(client_id, budget)
        finally:
            elapsed = time.monotonic() - started
            await ledger.close()
        assert elapsed < 2.0

    asyncio.run(exercise())


def test_migration_failure_rolls_back_partial_migration(
    tmp_path,
    monkeypatch,
) -> None:
    """
    RME

    Requires:
        - Dedicated PostgreSQL integration service is available.
        - tmp_path is writable for an isolated migration corpus.

    Modifies:
        - Attempts one valid temporary migration followed by one invalid temporary migration.
        - Temporarily replaces the migration directory used by app.database.

    Effects:
        - Verifies a failing migration invocation rolls back all new schema and migration metadata from that invocation.

    Inputs:
        - tmp_path: Pytest temporary directory.
        - monkeypatch: Pytest attribute patch helper.

    Outputs:
        - None. Assertions determine pass/fail.
    """
    database_url = _database_url()
    good_name = "910_m10_good.sql"
    bad_name = "911_m10_bad.sql"
    (tmp_path / good_name).write_text(
        "CREATE TABLE m10_migration_good (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (tmp_path / bad_name).write_text(
        "CREATE TABLE m10_migration_partial (id INTEGER PRIMARY KEY);\n"
        "SELECT * FROM m10_table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    asyncio.run(_initialize_database())
    monkeypatch.setattr(database, "_MIGRATION_DIRECTORY", tmp_path)

    async def exercise() -> None:
        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            await connection.execute("DROP TABLE IF EXISTS m10_migration_partial")
            await connection.execute("DROP TABLE IF EXISTS m10_migration_good")
            await connection.execute(
                "DELETE FROM schema_migrations WHERE filename IN (%s, %s)",
                (good_name, bad_name),
            )
        with pytest.raises(psycopg.Error):
            await database.migrate_database(database_url)

        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT filename FROM schema_migrations WHERE filename IN (%s, %s)",
                    (good_name, bad_name),
                )
                assert await cursor.fetchall() == []
                await cursor.execute("SELECT to_regclass('public.m10_migration_partial')")
                assert (await cursor.fetchone())[0] is None
                await cursor.execute("SELECT to_regclass('public.m10_migration_good')")
                assert (await cursor.fetchone())[0] is None

    asyncio.run(exercise())
