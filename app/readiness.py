import asyncio
import os

import psycopg
from redis.exceptions import RedisError

from app.redis_client import RedisClientConfigurationError, build_redis_client


async def _postgres_ready(database_url: str) -> bool:
    """
    RME

    Requires:
        - database_url identifies the configured PostgreSQL database.

    Modifies:
        - A short-lived PostgreSQL connection/session.

    Effects:
        - Verifies PostgreSQL connectivity and the required migrated gateway tables.
        - Returns False instead of exposing backend exception details.

    Inputs:
        - database_url: PostgreSQL connection string.

    Outputs:
        - True only when PostgreSQL is reachable and required schema objects exist.
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
    """
    RME

    Requires:
        - redis_url identifies the configured shared Redis/Valkey backend.

    Modifies:
        - A short-lived shared-backend client connection.

    Effects:
        - Pings Redis/Valkey using bounded connect/read timeouts.
        - Uses the same local or ElastiCache IAM/TLS authentication path as runtime controls.
        - Returns False instead of exposing backend exception details.

    Inputs:
        - redis_url: Redis-compatible connection URL.

    Outputs:
        - True only when the configured shared backend responds successfully.
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
    """
    RME

    Requires:
        - Runtime backend-selection environment variables describe the active deployment.

    Modifies:
        - Short-lived backend connection state for required shared services.

    Effects:
        - Checks only backends required by the current runtime configuration.
        - Treats missing backend URLs, failed connectivity, or missing PostgreSQL schema as not ready.
        - Returns safe component names without connection strings or exception contents.

    Inputs:
        - None.

    Outputs:
        - Tuple of overall readiness and safe unavailable-component names.
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
    """
    RME

    Requires:
        - Runtime backend environment variables describe the current deployment.

    Modifies:
        - Short-lived backend connection state and terminal output.

    Effects:
        - Runs the backend readiness checks for Kubernetes exec probes.
        - Exits zero when required backends and schema are ready, nonzero otherwise.
        - Prints only safe component names and never prints connection strings or credentials.

    Inputs:
        - None.

    Outputs:
        - Process exit status and a metadata-only readiness message.
    """
    ready, unavailable = asyncio.run(runtime_ready())
    if ready:
        print("READY")
        return

    print("NOT_READY components=" + ",".join(unavailable))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
