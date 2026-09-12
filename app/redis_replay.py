import os

from redis.exceptions import RedisError

from app.redis_client import RedisClientConfigurationError, build_redis_client
from app.tool_execution import (
    InMemoryToolExecutionReplayStore,
    ToolExecutionReplayStore,
    ToolExecutionUnavailable,
)


class SharedRedisToolExecutionReplayStore:
    def __init__(self, redis_url: str) -> None:
        """
        RME

        Requires:
            - redis_url identifies the configured shared Redis/Valkey service.

        Modifies:
            - Initializes a lazy shared Redis/Valkey client.

        Effects:
            - Uses the central Redis factory so TLS/IAM cloud authentication and local Redis behave consistently.

        Inputs:
            - redis_url: Redis-compatible connection URL.

        Outputs:
            - Distributed replay-store instance.
        """
        self._client = build_redis_client(redis_url, decode_responses=True)

    async def claim(self, execution_id: str, ttl_seconds: int) -> bool:
        """
        RME

        Requires:
            - execution_id is a validated execution-ticket identifier.
            - ttl_seconds is positive.

        Modifies:
            - Shared replay state under sag:tool_execution:*.

        Effects:
            - Atomically claims an execution ID with SET NX and expiry.
            - Fails closed if Redis/Valkey is unavailable.

        Inputs:
            - execution_id: Execution-ticket identifier.
            - ttl_seconds: Remaining ticket lifetime.

        Outputs:
            - True only for the first successful distributed claim.
        """
        try:
            result = await self._client.set(
                f"sag:tool_execution:{execution_id}",
                "1",
                ex=max(1, ttl_seconds),
                nx=True,
            )
        except RedisError as exc:
            raise ToolExecutionUnavailable("tool_execution_replay_store_unavailable") from exc
        return bool(result)

    async def close(self) -> None:
        """Close the shared Redis/Valkey client pool."""
        await self._client.aclose()


def build_runtime_tool_execution_replay_store() -> ToolExecutionReplayStore:
    """
    RME

    Requires:
        - SAG_TOOL_EXECUTION_REPLAY_BACKEND selects redis or memory.
        - REDIS_URL and Redis authentication settings are valid for redis mode.

    Modifies:
        - Nothing outside the returned replay-store object.

    Effects:
        - Uses distributed shared replay protection in runtime environments.
        - Retains the in-memory implementation only for deterministic tests.
        - Fails closed on unusable Redis/Valkey configuration.

    Inputs:
        - None.

    Outputs:
        - Configured ToolExecutionReplayStore.
    """
    backend = os.getenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "redis").strip().lower()
    if backend == "memory":
        return InMemoryToolExecutionReplayStore()
    if backend != "redis":
        raise ToolExecutionUnavailable("unsupported_tool_execution_replay_backend")

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    if not redis_url:
        raise ToolExecutionUnavailable("tool_execution_replay_store_not_configured")
    try:
        return SharedRedisToolExecutionReplayStore(redis_url)
    except RedisClientConfigurationError as exc:
        raise ToolExecutionUnavailable("tool_execution_replay_store_not_configured") from exc
