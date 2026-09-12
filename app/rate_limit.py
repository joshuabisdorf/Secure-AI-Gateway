import math
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from app.redis_client import RedisClientConfigurationError, build_redis_client

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_max_rate_limit_rpm = 1_000_000
_default_window_seconds = 60
_redis_key_prefix = "sag:rate_limit:"
_rate_limit_script = """
local current = redis.call('GET', KEYS[1])
if not current then
    redis.call('SET', KEYS[1], '1', 'EX', ARGV[2])
    return {1, 1, tonumber(ARGV[2])}
end

local ttl = redis.call('TTL', KEYS[1])
if ttl <= 0 then
    return redis.error_reply('rate_limit_key_without_expiry')
end

local current_number = tonumber(current)
local limit = tonumber(ARGV[1])
if not current_number or not limit then
    return redis.error_reply('invalid_rate_limit_state')
end

if current_number >= limit then
    return {current_number, 0, ttl}
end

local next_value = redis.call('INCR', KEYS[1])
return {next_value, 1, ttl}
""".strip()


class RateLimiterUnavailable(RuntimeError):
    """Raised when the configured rate-limit state backend cannot be used safely."""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit_rpm: int
    remaining: int
    retry_after_seconds: int | None = None


@dataclass
class _RateLimitBucket:
    window_started_at: float
    request_count: int


class _RedisClient(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object:
        ...

    async def aclose(self) -> None:
        ...


def parse_client_rate_limits(configured_limits: str) -> dict[str, int]:
    """
    RME

    Requires:
        - configured_limits uses client_id:requests_per_minute records separated by commas.

    Modifies:
        - Nothing.

    Effects:
        - Validates per-client rate-limit configuration.
        - Rejects malformed records, duplicate clients, and unusable limits.

    Inputs:
        - configured_limits: Serialized per-client requests-per-minute limits.

    Outputs:
        - Mapping from client ID to requests-per-minute limit.

    Raises:
        - ValueError: Configuration is empty, malformed, duplicated, or out of range.
    """
    limits: dict[str, int] = {}

    for raw_record in configured_limits.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":", 1)
        if len(parts) != 2:
            raise ValueError("invalid_rate_limit_record")

        client_id, raw_limit = (part.strip() for part in parts)
        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if not raw_limit.isdigit():
            raise ValueError("invalid_rate_limit")

        limit_rpm = int(raw_limit)
        if limit_rpm < 1 or limit_rpm > _max_rate_limit_rpm:
            raise ValueError("invalid_rate_limit")
        if client_id in limits:
            raise ValueError("duplicate_client_rate_limit")

        limits[client_id] = limit_rpm

    if not limits:
        raise ValueError("no_client_rate_limits")

    return limits


def get_client_rate_limit(client_id: str) -> int:
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_CLIENT_RATE_LIMITS may define per-client requests-per-minute limits.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed when rate-limit policy is absent, malformed, or missing the client.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Requests-per-minute limit configured for the client.
    """
    configured_limits = os.getenv("SAG_CLIENT_RATE_LIMITS")
    if not configured_limits:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limit policy is not configured.",
        )

    try:
        limits = parse_client_rate_limits(configured_limits)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limit policy is not configured.",
        ) from None

    limit_rpm = limits.get(client_id)
    if limit_rpm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limit policy is not configured for this client.",
        )

    return limit_rpm


class InMemoryRateLimiter:
    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """
        RME

        Requires:
            - window_seconds is greater than zero.
            - clock returns a monotonic timestamp in seconds.

        Modifies:
            - Initializes process-local rate-limit state.

        Effects:
            - Creates a fixed-window limiter used by deterministic tests.

        Inputs:
            - window_seconds: Duration of one rate-limit window.
            - clock: Monotonic time source, injectable for deterministic tests.

        Outputs:
            - A configured InMemoryRateLimiter instance.
        """
        if window_seconds <= 0:
            raise ValueError("invalid_window_seconds")

        self._window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, _RateLimitBucket] = {}
        self._lock = threading.Lock()

    async def check(self, client_id: str, limit_rpm: int) -> RateLimitDecision:
        """
        RME

        Requires:
            - client_id identifies an authenticated gateway client.
            - limit_rpm is a positive requests-per-minute limit.

        Modifies:
            - Process-local request-count state when a request is allowed.

        Effects:
            - Applies a fixed-window rate limit atomically for the current process.

        Inputs:
            - client_id: Authenticated gateway client identity.
            - limit_rpm: Maximum requests allowed during the current window.

        Outputs:
            - RateLimitDecision describing allow/deny, remaining capacity, and retry delay.
        """
        if limit_rpm < 1:
            raise ValueError("invalid_rate_limit")

        now = self._clock()

        with self._lock:
            bucket = self._buckets.get(client_id)
            if (
                bucket is None
                or now - bucket.window_started_at >= self._window_seconds
                or now < bucket.window_started_at
            ):
                bucket = _RateLimitBucket(
                    window_started_at=now,
                    request_count=0,
                )
                self._buckets[client_id] = bucket

            elapsed = max(0.0, now - bucket.window_started_at)
            if bucket.request_count >= limit_rpm:
                retry_after_seconds = max(
                    1,
                    math.ceil(self._window_seconds - elapsed),
                )
                return RateLimitDecision(
                    allowed=False,
                    limit_rpm=limit_rpm,
                    remaining=0,
                    retry_after_seconds=retry_after_seconds,
                )

            bucket.request_count += 1
            return RateLimitDecision(
                allowed=True,
                limit_rpm=limit_rpm,
                remaining=max(0, limit_rpm - bucket.request_count),
            )

    def reset(self) -> None:
        """Clear all process-local test rate-limit state."""
        with self._lock:
            self._buckets.clear()


class RedisRateLimiter:
    def __init__(
        self,
        redis_url: str,
        *,
        window_seconds: int = _default_window_seconds,
        client: _RedisClient | None = None,
    ) -> None:
        """
        RME

        Requires:
            - redis_url identifies a compatible Redis or Valkey server when client is not injected.
            - window_seconds is a positive integer.

        Modifies:
            - Initializes process-local shared-backend client/pool state.

        Effects:
            - Creates a lazily connected distributed fixed-window rate limiter.
            - Uses the central Redis factory for local Redis or TLS/IAM-authenticated ElastiCache.

        Inputs:
            - redis_url: Redis-compatible connection URL.
            - window_seconds: Fixed rate-limit window duration.
            - client: Optional compatible client for deterministic tests.

        Outputs:
            - A configured RedisRateLimiter instance.
        """
        if window_seconds < 1:
            raise ValueError("invalid_window_seconds")

        self._window_seconds = window_seconds
        self._client: _RedisClient = client or build_redis_client(
            redis_url,
            decode_responses=True,
        )

    async def check(self, client_id: str, limit_rpm: int) -> RateLimitDecision:
        """
        RME

        Requires:
            - The configured Redis/Valkey backend supports EVAL, GET, SET, TTL, and INCR.
            - client_id identifies an authenticated gateway client.
            - limit_rpm is a positive requests-per-minute limit.

        Modifies:
            - Shared fixed-window counter for the client when capacity remains.

        Effects:
            - Atomically increments and caps the shared counter with a portable Lua script.
            - Preserves the original window expiration instead of extending it per request.
            - Fails closed on malformed shared state or backend errors.

        Inputs:
            - client_id: Authenticated gateway client identity.
            - limit_rpm: Maximum requests allowed during the current window.

        Outputs:
            - RateLimitDecision shared across gateway processes and replicas.
        """
        if limit_rpm < 1:
            raise ValueError("invalid_rate_limit")

        key = f"{_redis_key_prefix}{client_id}"
        try:
            raw_result = await self._client.eval(
                _rate_limit_script,
                1,
                key,
                limit_rpm,
                self._window_seconds,
            )
            if not isinstance(raw_result, (list, tuple)) or len(raw_result) != 3:
                raise RateLimiterUnavailable("invalid_redis_rate_limit_response")

            current_value = int(raw_result[0])
            applied_increment = int(raw_result[1])
            ttl = int(raw_result[2])
        except (RedisError, TypeError, ValueError) as exc:
            raise RateLimiterUnavailable("rate_limiter_unavailable") from exc

        if current_value < 0 or applied_increment not in {0, 1} or ttl < 1:
            raise RateLimiterUnavailable("invalid_redis_rate_limit_response")

        allowed = applied_increment == 1
        remaining = max(0, limit_rpm - current_value)
        if allowed:
            return RateLimitDecision(
                allowed=True,
                limit_rpm=limit_rpm,
                remaining=remaining,
            )

        return RateLimitDecision(
            allowed=False,
            limit_rpm=limit_rpm,
            remaining=0,
            retry_after_seconds=ttl,
        )

    async def close(self) -> None:
        """Close the shared-backend client's connection pool."""
        await self._client.aclose()


class UnavailableRateLimiter:
    async def check(self, client_id: str, limit_rpm: int) -> RateLimitDecision:
        """Fail closed when no usable rate-limit backend is configured."""
        raise RateLimiterUnavailable("rate_limiter_not_configured")


def build_rate_limiter():
    """
    RME

    Requires:
        - SAG_RATE_LIMIT_BACKEND may select redis or memory.
        - REDIS_URL is configured when the Redis backend is selected.

    Modifies:
        - Initializes rate-limiter backend state.

    Effects:
        - Selects shared Redis/Valkey enforcement by default for runtime.
        - Retains the in-memory backend for deterministic tests.
        - Fails closed through UnavailableRateLimiter when configuration is unusable.

    Inputs:
        - None.

    Outputs:
        - Configured rate-limiter backend.
    """
    backend = os.getenv("SAG_RATE_LIMIT_BACKEND", "redis").strip().lower()

    if backend == "memory":
        return InMemoryRateLimiter()

    if backend == "redis":
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return UnavailableRateLimiter()
        try:
            return RedisRateLimiter(redis_url)
        except RedisClientConfigurationError:
            return UnavailableRateLimiter()

    return UnavailableRateLimiter()
