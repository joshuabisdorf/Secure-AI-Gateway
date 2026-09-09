import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, status

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_max_daily_tokens = 1_000_000_000
_max_daily_cost_usd = Decimal("1000000")


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


def parse_client_usage_budgets(
    configured_budgets: str,
) -> dict[str, ClientUsageBudget]:
    """
    RME

    Requires:
        - configured_budgets uses client_id:daily_tokens:daily_cost_usd records.
        - A hyphen may disable either the token or cost dimension.

    Modifies:
        - Nothing.

    Effects:
        - Validates per-client daily usage-budget configuration.
        - Rejects malformed records, duplicate clients, and unusable limits.

    Inputs:
        - configured_budgets: Serialized client daily token and cost budgets.

    Outputs:
        - Mapping from client ID to validated ClientUsageBudget.

    Raises:
        - ValueError: Configuration is empty, malformed, duplicated, or out of range.
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
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_CLIENT_DAILY_BUDGETS may define per-client daily usage limits.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed when budget policy is absent, malformed, or missing the client.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Daily token and/or cost budget configured for the client.
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


class InMemoryUsageLedger:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """
        RME

        Requires:
            - clock returns timezone-aware datetimes.

        Modifies:
            - Initializes process-local per-client usage state.

        Effects:
            - Creates a UTC-day usage ledger suitable for single-process development.

        Inputs:
            - clock: UTC-aware time source, injectable for deterministic tests.

        Outputs:
            - A configured InMemoryUsageLedger instance.
        """
        self._clock = clock
        self._buckets: dict[str, _UsageBucket] = {}
        self._lock = threading.Lock()

    def check(
        self,
        client_id: str,
        budget: ClientUsageBudget,
    ) -> UsageBudgetDecision:
        """
        RME

        Requires:
            - client_id identifies an authenticated gateway client.
            - budget has at least one positive daily limit.

        Modifies:
            - Initializes or rolls over the client's UTC-day bucket when necessary.

        Effects:
            - Determines whether the client has remaining daily token and cost capacity.

        Inputs:
            - client_id: Authenticated gateway client identity.
            - budget: Daily usage limits configured for the client.

        Outputs:
            - UsageBudgetDecision describing current usage and whether forwarding is allowed.
        """
        now = self._now_utc()
        with self._lock:
            bucket = self._get_bucket(client_id, now)
            return self._decision(bucket, budget, now)

    def record(
        self,
        client_id: str,
        budget: ClientUsageBudget,
        *,
        total_tokens: int,
        cost_usd: Decimal,
    ) -> UsageBudgetDecision:
        """
        RME

        Requires:
            - client_id identifies an authenticated gateway client.
            - total_tokens is non-negative.
            - cost_usd is finite and non-negative.

        Modifies:
            - Process-local daily token and cost totals for the client.

        Effects:
            - Atomically records provider-reported usage for the current UTC day.

        Inputs:
            - client_id: Authenticated gateway client identity.
            - budget: Daily usage limits configured for the client.
            - total_tokens: Provider-reported tokens consumed by the request.
            - cost_usd: Provider-reported request cost in USD.

        Outputs:
            - UsageBudgetDecision containing updated cumulative usage.
        """
        if total_tokens < 0:
            raise ValueError("invalid_total_tokens")
        if not cost_usd.is_finite() or cost_usd < 0:
            raise ValueError("invalid_cost")

        now = self._now_utc()
        with self._lock:
            bucket = self._get_bucket(client_id, now)
            bucket.tokens_used += total_tokens
            bucket.cost_used_usd += cost_usd
            return self._decision(bucket, budget, now)

    def reset(self) -> None:
        """
        RME

        Requires:
            - Nothing.

        Modifies:
            - Process-local usage-ledger state.

        Effects:
            - Clears all tracked client usage.

        Inputs:
            - None.

        Outputs:
            - None.
        """
        with self._lock:
            self._buckets.clear()

    def _now_utc(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock_must_be_timezone_aware")
        return now.astimezone(timezone.utc)

    def _get_bucket(self, client_id: str, now: datetime) -> _UsageBucket:
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

    @staticmethod
    def _reset_at(now: datetime) -> datetime:
        next_day = now.date().toordinal() + 1
        return datetime.fromordinal(next_day).replace(tzinfo=timezone.utc)

    def _decision(
        self,
        bucket: _UsageBucket,
        budget: ClientUsageBudget,
        now: datetime,
    ) -> UsageBudgetDecision:
        token_remaining = (
            None
            if budget.token_limit_daily is None
            else max(0, budget.token_limit_daily - bucket.tokens_used)
        )
        cost_remaining = (
            None
            if budget.cost_limit_daily_usd is None
            else max(
                Decimal("0"),
                budget.cost_limit_daily_usd - bucket.cost_used_usd,
            )
        )

        reason = None
        if (
            budget.token_limit_daily is not None
            and bucket.tokens_used >= budget.token_limit_daily
        ):
            reason = "token_budget_exceeded"
        elif (
            budget.cost_limit_daily_usd is not None
            and bucket.cost_used_usd >= budget.cost_limit_daily_usd
        ):
            reason = "cost_budget_exceeded"

        return UsageBudgetDecision(
            allowed=reason is None,
            token_limit_daily=budget.token_limit_daily,
            tokens_used_daily=bucket.tokens_used,
            tokens_remaining_daily=token_remaining,
            cost_limit_daily_usd=budget.cost_limit_daily_usd,
            cost_used_daily_usd=bucket.cost_used_usd,
            cost_remaining_daily_usd=cost_remaining,
            reset_at=self._reset_at(now),
            reason=reason,
        )
