import asyncio
import os

import psycopg
from redis.exceptions import RedisError

from app.redis_client import RedisClientConfigurationError, build_redis_client


async def _postgres_ready(database_url: str) -> bool:
    """Verifie PostgreSQL connectivity and the required migrated gateway tables.

    Args:
        database_url: PostgreSQL connection string.

    Returns:
        True only when PostgreSQL is reachable and required schema objects
        exist.

    Notes:
        - Precondition: database_url identifies the configured PostgreSQL
          database.
        - Side effect: A short-lived PostgreSQL connection/session.
        - Returns False instead of exposing backend exception details.
    """
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=2,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        to_regclass('public.gateway_clients') IS NOT NULL,
                        to_regclass('public.gateway_api_keys') IS NOT NULL,
                        to_regclass('public.gateway_daily_usage') IS NOT NULL
                    """
                )
                row = await cursor.fetchone()
    except (OSError, psycopg.Error):
        return False

    return row == (True, True, True)


async def _redis_ready(redis_url: str) -> bool:
    """Ping Redis/Valkey using bounded connect/read timeouts.

    Args:
        redis_url: Redis-compatible connection URL.

    Returns:
        True only when the configured shared backend responds successfully.

    Notes:
        - Precondition: redis_url identifies the configured shared Redis/Valkey
          backend.
        - Side effect: A short-lived shared-backend client connection.
        - Uses the same local or ElastiCache IAM/TLS authentication path as
          runtime controls.
        - Returns False instead of exposing backend exception details.
    """
    try:
        client = build_redis_client(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    except RedisClientConfigurationError:
        return False

    try:
        return bool(await client.ping())
    except (OSError, RedisError):
        return False
    finally:
        await client.aclose()


def _postgres_required() -> bool:
    """Return whether any configured runtime control requires PostgreSQL."""
    return any(
        value.strip().lower() == "postgres"
        for value in (
            os.getenv("SAG_CLIENT_REGISTRY_BACKEND", "postgres"),
            os.getenv("SAG_USAGE_LEDGER_BACKEND", "postgres"),
        )
    )


def _redis_required() -> bool:
    """Return whether any configured runtime control requires Redis/Valkey."""
    return any(
        value.strip().lower() == "redis"
        for value in (
            os.getenv("SAG_RATE_LIMIT_BACKEND", "redis"),
            os.getenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "redis"),
        )
    )


async def runtime_ready() -> tuple[bool, tuple[str, ...]]:
    """Check only backends required by the current runtime configuration.

    Returns:
        Tuple of overall readiness and safe unavailable-component names.

    Notes:
        - Precondition: Runtime backend-selection environment variables
          describe the active deployment.
        - Side effect: Short-lived backend connection state for required shared
          services.
        - Treats missing backend URLs, failed connectivity, or missing
        - PostgreSQL schema as not ready.
        - Returns safe component names without connection strings or exception
          contents.
    """
    unavailable: list[str] = []

    if _postgres_required():
        database_url = os.getenv("DATABASE_URL")
        if not database_url or not await _postgres_ready(database_url):
            unavailable.append("postgres")

    if _redis_required():
        redis_url = os.getenv("REDIS_URL")
        if not redis_url or not await _redis_ready(redis_url):
            unavailable.append("redis")

    return (not unavailable, tuple(unavailable))


def main() -> None:
    """Run the backend readiness checks for Kubernetes exec probes.

    Returns:
        Process exit status and a metadata-only readiness message.

    Notes:
        - Precondition: Runtime backend environment variables describe the
          current deployment.
        - Side effect: Short-lived backend connection state and terminal
          output.
        - Exits zero when required backends and schema are ready, nonzero
          otherwise.
        - Prints only safe component names and never prints connection strings
          or credentials.
    """
    ready, unavailable = asyncio.run(runtime_ready())
    if ready:
        print("READY")
        return

    print("NOT_READY components=" + ",".join(unavailable))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
