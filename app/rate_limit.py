import math
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from fastapi import HTTPException, status

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_max_rate_limit_rpm = 1_000_000


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
            - Creates a fixed-window rate limiter suitable for single-process development.

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

    def check(self, client_id: str, limit_rpm: int) -> RateLimitDecision:
        """
        RME

        Requires:
            - client_id identifies an authenticated gateway client.
            - limit_rpm is a positive requests-per-minute limit.

        Modifies:
            - Process-local request-count state for the client when a request is allowed.

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
        """
        RME

        Requires:
            - Nothing.

        Modifies:
            - Process-local rate-limit bucket state.

        Effects:
            - Clears all tracked client request counts.

        Inputs:
            - None.

        Outputs:
            - None.
        """
        with self._lock:
            self._buckets.clear()
