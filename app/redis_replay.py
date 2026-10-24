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
        """Use the central Redis factory so TLS/IAM cloud authentication and
            local Redis
        behave consistently.

        :param redis_url: Redis-compatible connection URL.
        :returns: Distributed replay-store instance.

        .. note::

            - Precondition: redis_url identifies the configured shared
              Redis/Valkey service.
            - Side effect: Initializes a lazy shared Redis/Valkey client.
        """
        self._client = build_redis_client(redis_url, decode_responses=True)

    async def claim(self, execution_id: str, ttl_seconds: int) -> bool:
        """Atomically claims an execution ID with SET NX and expiry.

        :param execution_id: Execution-ticket identifier.
        :param ttl_seconds: Remaining ticket lifetime.
        :returns: True only for the first successful distributed claim.

        .. note::

            - Precondition: execution_id is a validated execution-ticket
              identifier.
            - Precondition: ttl_seconds is positive.
            - Side effect: Shared replay state under sag:tool_execution:*.
            - Fails closed if Redis/Valkey is unavailable.
        """
        try:
            result = await self._client.set(
                f"sag:tool_execution:{execution_id}",
                "1",
                ex=max(1, ttl_seconds),
                nx=True,
            )
        except RedisError as exc:
            raise ToolExecutionUnavailable(
                "tool_execution_replay_store_unavailable"
            ) from exc
        return bool(result)

    async def close(self) -> None:
        """Close the shared Redis/Valkey client pool.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
            - Side effect: Owned runtime or dependency state, as described by
              the operation.
        """
        await self._client.aclose()


def build_runtime_tool_execution_replay_store() -> ToolExecutionReplayStore:
    """Use distributed shared replay protection in runtime environments.

    :returns: Configured ToolExecutionReplayStore.

    .. note::

        - Precondition: SAG_TOOL_EXECUTION_REPLAY_BACKEND selects redis or
          memory.
        - Precondition: REDIS_URL and Redis authentication settings are valid
          for redis mode.
        - Side effect: Nothing outside the returned replay-store object.
        - Retains the in-memory implementation only for deterministic tests.
        - Fails closed on unusable Redis/Valkey configuration.
    """
    backend = (
        os.getenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "redis").strip().lower()
    )
    if backend == "memory":
        return InMemoryToolExecutionReplayStore()
    if backend != "redis":
        raise ToolExecutionUnavailable(
            "unsupported_tool_execution_replay_backend"
        )

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    if not redis_url:
        raise ToolExecutionUnavailable(
            "tool_execution_replay_store_not_configured"
        )
    try:
        return SharedRedisToolExecutionReplayStore(redis_url)
    except RedisClientConfigurationError as exc:
        raise ToolExecutionUnavailable(
            "tool_execution_replay_store_not_configured"
        ) from exc
