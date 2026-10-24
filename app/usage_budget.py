"""Enforce token and spending budgets with persistent accounting."""

import asyncio
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

from fastapi import HTTPException, status
from psycopg import Error as PsycopgError
from psycopg_pool import AsyncConnectionPool, PoolTimeout

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_max_daily_tokens = 1_000_000_000
_max_daily_cost_usd = Decimal("1000000")
_default_connection_timeout_seconds = 5.0


@dataclass(frozen=True)
class ClientUsageBudget:
    token_limit_daily: int | None
    cost_limit_daily_usd: Decimal | None


@dataclass(frozen=True)
class UsageBudgetDecision:
    allowed: bool
    token_limit_daily: int | None
    tokens_used_daily: int
    tokens_remaining_daily: int | None
    cost_limit_daily_usd: Decimal | None
    cost_used_daily_usd: Decimal
    cost_remaining_daily_usd: Decimal | None
    reset_at: datetime
    reason: str | None = None


@dataclass
class _UsageBucket:
    day: str
    tokens_used: int
    cost_used_usd: Decimal


class UsageLedgerUnavailable(RuntimeError):
    """Raised when durable usage state cannot be read or recorded safely."""


class UsageLedger(Protocol):
    async def check(
        self,
        client_id: str,
        budget: ClientUsageBudget,
    ) -> UsageBudgetDecision:
        """Return current daily usage and whether another request may be
            forwarded.        :param client_id: Function input.
        :param budget: Function input.
        :returns: A value matching the declared UsageBudgetDecision return
            contract.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
        """
        ...

    async def record(
        self,
        client_id: str,
        budget: ClientUsageBudget,
        *,
        total_tokens: int,
        cost_usd: Decimal,
    ) -> UsageBudgetDecision:
        """Atomically add provider-reported usage and return updated daily
            totals.        :param client_id: Function input.
        :param budget: Function input.
        :param total_tokens: Function input.
        :param cost_usd: Function input.
        :returns: A value matching the declared UsageBudgetDecision return
            contract.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
            - Side effect: Owned runtime or dependency state, as described by
              the operation.
        """
        ...


def parse_client_usage_budgets(
    configured_budgets: str,
) -> dict[str, ClientUsageBudget]:
    """Validate per-client daily usage-budget configuration.

    :param configured_budgets: Serialized client daily token and cost budgets.
    :returns: Mapping from client ID to validated ClientUsageBudget.
    :raises ValueError: Configuration is empty, malformed, duplicated, or out
        of range.

    .. note::

        - Precondition: configured_budgets uses
          client_id:daily_tokens:daily_cost_usd records.
        - Precondition: A hyphen may disable either the token or cost
          dimension.
        - Rejects malformed records, duplicate clients, and unusable limits.
    """
    budgets: dict[str, ClientUsageBudget] = {}

    for raw_record in configured_budgets.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":")
        if len(parts) != 3:
            raise ValueError("invalid_usage_budget_record")

        client_id, raw_tokens, raw_cost = (part.strip() for part in parts)
        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if client_id in budgets:
            raise ValueError("duplicate_client_usage_budget")

        token_limit: int | None
        if raw_tokens == "-":
            token_limit = None
        elif raw_tokens.isdigit():
            token_limit = int(raw_tokens)
            if token_limit < 1 or token_limit > _max_daily_tokens:
                raise ValueError("invalid_token_budget")
        else:
            raise ValueError("invalid_token_budget")

        cost_limit: Decimal | None
        if raw_cost == "-":
            cost_limit = None
        else:
            try:
                cost_limit = Decimal(raw_cost)
            except InvalidOperation:
                raise ValueError("invalid_cost_budget") from None

            if (
                not cost_limit.is_finite()
                or cost_limit <= 0
                or cost_limit > _max_daily_cost_usd
            ):
                raise ValueError("invalid_cost_budget")

        if token_limit is None and cost_limit is None:
            raise ValueError("empty_usage_budget")

        budgets[client_id] = ClientUsageBudget(
            token_limit_daily=token_limit,
            cost_limit_daily_usd=cost_limit,
        )

    if not budgets:
        raise ValueError("no_client_usage_budgets")

    return budgets


def get_client_usage_budget(client_id: str) -> ClientUsageBudget:
    """Fail closed when budget policy is absent, malformed, or missing the
        client.    :param client_id: Authenticated gateway client identity.
    :returns: Daily token and/or cost budget configured for the client.

    .. note::

        - Precondition: client_id identifies an authenticated gateway client.
        - Precondition: SAG_CLIENT_DAILY_BUDGETS may define per-client daily
          usage limits.
    """
    configured_budgets = os.getenv("SAG_CLIENT_DAILY_BUDGETS")
    if not configured_budgets:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Usage budget policy is not configured.",
        )

    try:
        budgets = parse_client_usage_budgets(configured_budgets)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Usage budget policy is not configured.",
        ) from None

    budget = budgets.get(client_id)
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Usage budget policy is not configured for this client.",
        )

    return budget


def _now_utc(clock: Callable[[], datetime]) -> datetime:
    """Normalize the supplied time source to UTC.

    :param clock: Time source callable.
    :returns: UTC-aware current datetime.

    .. note::

        - Precondition: clock returns a timezone-aware datetime.
    """
    now = clock()
    if now.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return now.astimezone(timezone.utc)


def _build_decision(
    *,
    tokens_used: int,
    cost_used_usd: Decimal,
    budget: ClientUsageBudget,
    now: datetime,
) -> UsageBudgetDecision:
    """Compute remaining daily capacity and the next UTC reset boundary.

    :param tokens_used: Current UTC-day token total.
    :param cost_used_usd: Current UTC-day cost total.
    :param budget: Client usage limits.
    :param now: Current UTC time.
    :returns: UsageBudgetDecision describing current allowance and remaining
        capacity.

    .. note::

        - Precondition: Usage totals are non-negative.
        - Precondition: now is the normalized current UTC time.
    """
    token_remaining = (
        None
        if budget.token_limit_daily is None
        else max(0, budget.token_limit_daily - tokens_used)
    )
    cost_remaining = (
        None
        if budget.cost_limit_daily_usd is None
        else max(Decimal("0"), budget.cost_limit_daily_usd - cost_used_usd)
    )

    reason = None
    if (
        budget.token_limit_daily is not None
        and tokens_used >= budget.token_limit_daily
    ):
        reason = "token_budget_exceeded"
    elif (
        budget.cost_limit_daily_usd is not None
        and cost_used_usd >= budget.cost_limit_daily_usd
    ):
        reason = "cost_budget_exceeded"

    reset_at = datetime.combine(
        now.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return UsageBudgetDecision(
        allowed=reason is None,
        token_limit_daily=budget.token_limit_daily,
        tokens_used_daily=tokens_used,
        tokens_remaining_daily=token_remaining,
        cost_limit_daily_usd=budget.cost_limit_daily_usd,
        cost_used_daily_usd=cost_used_usd,
        cost_remaining_daily_usd=cost_remaining,
        reset_at=reset_at,
        reason=reason,
    )


class InMemoryUsageLedger:
    """Deterministic test ledger; runtime uses PostgreSQL by default."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """Create a UTC-day usage ledger for deterministic tests.

        :param clock: UTC-aware time source, injectable for deterministic
            tests.

        .. note::

            - Precondition: clock returns timezone-aware datetimes.
            - Side effect: Initializes process-local per-client usage state.
        """
        self._clock = clock
        self._buckets: dict[str, _UsageBucket] = {}
        self._lock = threading.Lock()

    async def check(
        self,
        client_id: str,
        budget: ClientUsageBudget,
    ) -> UsageBudgetDecision:
        """Read deterministic in-memory usage for the current UTC day.

        :param client_id: Client identity.
        :param budget: Client usage limits.
        :returns: Current UsageBudgetDecision.

        .. note::

            - Precondition: client_id identifies a test client.
            - Side effect: May initialize the current-day process-local bucket.
        """
        now = _now_utc(self._clock)
        with self._lock:
            bucket = self._get_bucket(client_id, now)
            return _build_decision(
                tokens_used=bucket.tokens_used,
                cost_used_usd=bucket.cost_used_usd,
                budget=budget,
                now=now,
            )

    async def record(
        self,
        client_id: str,
        budget: ClientUsageBudget,
        *,
        total_tokens: int,
        cost_usd: Decimal,
    ) -> UsageBudgetDecision:
        """Atomically adds usage under a process lock.

        :param client_id: Client identity.
        :param budget: Client usage limits.
        :param total_tokens: Tokens to add.
        :param cost_usd: Cost to add.
        :returns: Updated UsageBudgetDecision.

        .. note::

            - Precondition: total_tokens and cost_usd are non-negative usage
              values.
            - Side effect: Current-day process-local client usage totals.
        """
        _validate_usage(total_tokens=total_tokens, cost_usd=cost_usd)
        now = _now_utc(self._clock)
        with self._lock:
            bucket = self._get_bucket(client_id, now)
            bucket.tokens_used += total_tokens
            bucket.cost_used_usd += cost_usd
            return _build_decision(
                tokens_used=bucket.tokens_used,
                cost_used_usd=bucket.cost_used_usd,
                budget=budget,
                now=now,
            )

    def reset(self) -> None:
        """Clear process-local test usage state.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
            - Side effect: Owned runtime or dependency state, as described by
              the operation.
        """
        with self._lock:
            self._buckets.clear()

    def _get_bucket(self, client_id: str, now: datetime) -> _UsageBucket:
        """Reset usage automatically at a UTC day boundary.

        :param client_id: Client identity.
        :param now: Current UTC time.
        :returns: Current process-local usage bucket.

        .. note::

            - Precondition: now is UTC-aware.
            - Side effect: Process-local bucket mapping when the client/day has
              no current bucket.
        """
        day = now.date().isoformat()
        bucket = self._buckets.get(client_id)
        if bucket is None or bucket.day != day:
            bucket = _UsageBucket(
                day=day,
                tokens_used=0,
                cost_used_usd=Decimal("0"),
            )
            self._buckets[client_id] = bucket
        return bucket


class PostgresUsageLedger:
    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        connection_timeout_seconds: float = _default_connection_timeout_seconds,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """Create a closed async pool and defer connections until first use.

        :param database_url: PostgreSQL connection string.
        :param min_size: Minimum pooled connections.
        :param max_size: Maximum pooled connections.
        :param connection_timeout_seconds: Maximum wait for a pooled
            connection.
        :param clock: UTC-aware time source.

        .. note::

            - Precondition: database_url identifies the gateway PostgreSQL
              database.
            - Precondition: min_size and max_size define a valid
              connection-pool range.
            - Precondition: connection_timeout_seconds is greater than zero.
            - Precondition: clock returns timezone-aware datetimes.
            - Side effect: Initializes process-local PostgreSQL pool state.
            - Bounds pool acquisition waits so exhaustion fails closed instead
              of stalling indefinitely.
        """
        if connection_timeout_seconds <= 0:
            raise ValueError("invalid_connection_timeout_seconds")
        self._connection_timeout_seconds = connection_timeout_seconds
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=connection_timeout_seconds,
            open=False,
        )
        self._open_lock = asyncio.Lock()
        self._is_open = False
        self._clock = clock

    async def _ensure_open(self) -> None:
        """Open the pool once under an async lock.

        .. note::

            - Precondition: The configured PostgreSQL service may be opened
              lazily.
            - Side effect: PostgreSQL pool open state.
            - Converts database/pool timeout failures to
              UsageLedgerUnavailable.
        """
        if self._is_open:
            return

        async with self._open_lock:
            if self._is_open:
                return
            try:
                await self._pool.open(
                    wait=True,
                    timeout=self._connection_timeout_seconds,
                )
            except (PsycopgError, PoolTimeout) as exc:
                raise UsageLedgerUnavailable(
                    "usage_ledger_unavailable"
                ) from exc
            self._is_open = True

    async def check(
        self,
        client_id: str,
        budget: ClientUsageBudget,
    ) -> UsageBudgetDecision:
        """Read persisted usage for the current UTC day.

        :param client_id: Authenticated client identity.
        :param budget: Configured daily token/cost limits.
        :returns: Current daily UsageBudgetDecision.

        .. note::

            - Precondition: gateway_daily_usage schema has been initialized.
            - Precondition: client_id identifies a gateway client.
            - Side effect: PostgreSQL connection-pool/query state only.
            - Treats a missing daily row as zero accumulated usage.
            - Fails closed when PostgreSQL cannot be queried or a connection
              cannot be acquired within the configured timeout.
        """
        now = _now_utc(self._clock)
        await self._ensure_open()

        try:
            async with self._pool.connection(
                timeout=self._connection_timeout_seconds
            ) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT tokens_used, cost_used_usd
                        FROM gateway_daily_usage
                        WHERE client_id = %s
                          AND usage_date = %s
                        """,
                        (client_id, now.date()),
                    )
                    row = await cursor.fetchone()
        except (PsycopgError, PoolTimeout) as exc:
            raise UsageLedgerUnavailable("usage_ledger_unavailable") from exc

        tokens_used = 0 if row is None else int(row[0])
        cost_used = Decimal("0") if row is None else Decimal(row[1])
        return _build_decision(
            tokens_used=tokens_used,
            cost_used_usd=cost_used,
            budget=budget,
            now=now,
        )

    async def record(
        self,
        client_id: str,
        budget: ClientUsageBudget,
        *,
        total_tokens: int,
        cost_usd: Decimal,
    ) -> UsageBudgetDecision:
        """Atomically inserts or increments durable token/cost totals.

        :param client_id: Authenticated client identity.
        :param budget: Configured daily token/cost limits.
        :param total_tokens: Provider-reported tokens for the completed
            request.
        :param cost_usd: Provider-reported cost, or zero when cost is
            unavailable and not required.
        :returns: Updated daily UsageBudgetDecision.

        .. note::

            - Precondition: gateway_daily_usage schema has been initialized.
            - Precondition: client_id identifies a stored gateway client.
            - Precondition: total_tokens and cost_usd are non-negative
              provider-reported usage.
            - Side effect: gateway_daily_usage for the client and current UTC
              day.
            - Prevents concurrent successful requests from losing increments.
            - Fails closed when usage cannot be persisted or a connection
              cannot be acquired within the configured timeout.
        """
        _validate_usage(total_tokens=total_tokens, cost_usd=cost_usd)
        now = _now_utc(self._clock)
        await self._ensure_open()

        try:
            async with self._pool.connection(
                timeout=self._connection_timeout_seconds
            ) as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO gateway_daily_usage (
                            client_id,
                            usage_date,
                            tokens_used,
                            cost_used_usd
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (client_id, usage_date)
                        DO UPDATE SET
                            tokens_used = gateway_daily_usage.tokens_used + \
                                EXCLUDED.tokens_used,
                            cost_used_usd = \
                                gateway_daily_usage.cost_used_usd + \
                                    EXCLUDED.cost_used_usd,
                            updated_at = NOW()
                        RETURNING tokens_used, cost_used_usd
                        """,
                        (client_id, now.date(), total_tokens, cost_usd),
                    )
                    row = await cursor.fetchone()
        except (PsycopgError, PoolTimeout) as exc:
            raise UsageLedgerUnavailable("usage_ledger_unavailable") from exc

        if row is None:
            raise UsageLedgerUnavailable("usage_ledger_update_failed")

        return _build_decision(
            tokens_used=int(row[0]),
            cost_used_usd=Decimal(row[1]),
            budget=budget,
            now=now,
        )

    async def close(self) -> None:
        """Close the PostgreSQL pool when it was opened.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
            - Side effect: Owned runtime or dependency state, as described by
              the operation.
        """
        if self._is_open:
            await self._pool.close()
            self._is_open = False


class UnavailableUsageLedger:
    async def check(
        self,
        client_id: str,
        budget: ClientUsageBudget,
    ) -> UsageBudgetDecision:
        """Fail closed when no usable durable ledger is configured.

        :param client_id: Function input.
        :param budget: Function input.
        :returns: A value matching the declared UsageBudgetDecision return
            contract.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
        """
        raise UsageLedgerUnavailable("usage_ledger_not_configured")

    async def record(
        self,
        client_id: str,
        budget: ClientUsageBudget,
        *,
        total_tokens: int,
        cost_usd: Decimal,
    ) -> UsageBudgetDecision:
        """Fail closed when no usable durable ledger is configured.

        :param client_id: Function input.
        :param budget: Function input.
        :param total_tokens: Function input.
        :param cost_usd: Function input.
        :returns: A value matching the declared UsageBudgetDecision return
            contract.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
            - Side effect: Owned runtime or dependency state, as described by
              the operation.
        """
        raise UsageLedgerUnavailable("usage_ledger_not_configured")


def build_usage_ledger() -> UsageLedger:
    """Select durable PostgreSQL usage accounting by default.

    :returns: Configured UsageLedger implementation.

    .. note::

        - Precondition: SAG_USAGE_LEDGER_BACKEND may select postgres or memory.
        - Precondition: DATABASE_URL is configured when PostgreSQL is selected.
        - Side effect: Initializes ledger object state.
        - Retains the in-memory backend for deterministic tests.
        - Applies a bounded default PostgreSQL pool wait.
        - Fails closed through UnavailableUsageLedger for unusable
          configuration.
    """
    backend = os.getenv("SAG_USAGE_LEDGER_BACKEND", "postgres").strip().lower()
    if backend == "memory":
        return InMemoryUsageLedger()
    if backend == "postgres":
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            return PostgresUsageLedger(database_url)
        return UnavailableUsageLedger()
    return UnavailableUsageLedger()


def _validate_usage(*, total_tokens: int, cost_usd: Decimal) -> None:
    """Reject negative, infinite, or NaN usage before persistence.

    :param total_tokens: Token count.
    :param cost_usd: Decimal cost.
    :returns: None when values are valid.

    .. note::

        - Precondition: total_tokens and cost_usd are candidate
          provider-reported usage values.
    """
    if total_tokens < 0:
        raise ValueError("invalid_total_tokens")
    if not cost_usd.is_finite() or cost_usd < 0:
        raise ValueError("invalid_cost")
