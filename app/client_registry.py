import asyncio
import os
from typing import Protocol

from psycopg import Error as PsycopgError
from psycopg_pool import AsyncConnectionPool

from app.api_keys import ClientKeyRecord, parse_client_records


class ClientRegistryUnavailable(RuntimeError):
    """Raised when the configured client registry cannot be queried safely."""


class ClientRegistry(Protocol):
    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """
        RME

        Requires:
            - key_id is a validated public gateway key identifier.

        Modifies:
            - Registry-specific connection or query state, if any.

        Effects:
            - Looks up an active gateway key and its active client identity.

        Inputs:
            - key_id: Public identifier embedded in a structured gateway API key.

        Outputs:
            - Matching ClientKeyRecord, or None when no active key exists.
        """
        ...


class EnvironmentClientRegistry:
    """Legacy/test registry backed by SAG_CLIENTS rather than persistent storage."""

    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """
        RME

        Requires:
            - SAG_CLIENTS may contain serialized hashed client-key records.

        Modifies:
            - Nothing.

        Effects:
            - Parses the environment-backed registry for deterministic tests.
            - Fails closed when the registry is missing or malformed.

        Inputs:
            - key_id: Public key identifier to look up.

        Outputs:
            - Matching ClientKeyRecord, or None when the key ID is unknown.
        """
        configured_clients = os.getenv("SAG_CLIENTS")
        if not configured_clients:
            raise ClientRegistryUnavailable("client_registry_not_configured")

        try:
            records = parse_client_records(configured_clients)
        except ValueError as exc:
            raise ClientRegistryUnavailable("client_registry_invalid") from exc

        return records.get(key_id)


class PostgresClientRegistry:
    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        """
        RME

        Requires:
            - database_url is a PostgreSQL connection string.
            - min_size and max_size define a valid connection-pool range.

        Modifies:
            - Initializes process-local PostgreSQL pool state.

        Effects:
            - Creates a closed async PostgreSQL connection pool.
            - Defers network connections until the first registry query.

        Inputs:
            - database_url: PostgreSQL connection string.
            - min_size: Minimum number of pooled connections.
            - max_size: Maximum number of pooled connections.

        Outputs:
            - A configured PostgresClientRegistry instance.
        """
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            open=False,
        )
        self._open_lock = asyncio.Lock()
        self._is_open = False

    async def _ensure_open(self) -> None:
        if self._is_open:
            return

        async with self._open_lock:
            if self._is_open:
                return

            try:
                await self._pool.open(wait=True)
            except PsycopgError as exc:
                raise ClientRegistryUnavailable("client_registry_unavailable") from exc

            self._is_open = True

    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """
        RME

        Requires:
            - key_id is a validated public gateway key identifier.
            - Database schema for gateway_clients and gateway_api_keys has been initialized.

        Modifies:
            - PostgreSQL connection-pool state.

        Effects:
            - Opens the pool on first use.
            - Queries only active keys belonging to active clients.
            - Converts database failures into a non-secret registry error.

        Inputs:
            - key_id: Public key identifier to look up.

        Outputs:
            - Matching ClientKeyRecord, or None when no active key exists.
        """
        await self._ensure_open()

        try:
            async with self._pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT k.client_id, k.key_id, k.api_key_sha256
                        FROM gateway_api_keys AS k
                        JOIN gateway_clients AS c
                          ON c.client_id = k.client_id
                        WHERE k.key_id = %s
                          AND k.is_active = TRUE
                          AND c.is_active = TRUE
                        """,
                        (key_id,),
                    )
                    row = await cursor.fetchone()
        except PsycopgError as exc:
            raise ClientRegistryUnavailable("client_registry_unavailable") from exc

        if row is None:
            return None

        client_id, resolved_key_id, api_key_sha256 = row
        return ClientKeyRecord(
            client_id=str(client_id),
            key_id=str(resolved_key_id),
            api_key_sha256=str(api_key_sha256),
        )

    async def close(self) -> None:
        """
        RME

        Requires:
            - The registry may have opened its process-local connection pool.

        Modifies:
            - PostgreSQL connection-pool state.

        Effects:
            - Closes pooled database connections when the pool was opened.

        Inputs:
            - None.

        Outputs:
            - None.
        """
        if self._is_open:
            await self._pool.close()
            self._is_open = False


class UnavailableClientRegistry:
    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """Fail closed when no usable registry backend is configured."""
        raise ClientRegistryUnavailable("client_registry_not_configured")


def build_client_registry() -> ClientRegistry:
    """
    RME

    Requires:
        - SAG_CLIENT_REGISTRY_BACKEND may select postgres or environment.
        - DATABASE_URL is configured when the postgres backend is selected.

    Modifies:
        - Initializes registry object state.

    Effects:
        - Selects persistent PostgreSQL storage by default.
        - Retains an explicit environment backend for deterministic tests only.
        - Fails closed through UnavailableClientRegistry for unusable configuration.

    Inputs:
        - None.

    Outputs:
        - Configured ClientRegistry implementation.
    """
    backend = os.getenv("SAG_CLIENT_REGISTRY_BACKEND", "postgres").strip().lower()

    if backend == "environment":
        return EnvironmentClientRegistry()

    if backend == "postgres":
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            return PostgresClientRegistry(database_url)
        return UnavailableClientRegistry()

    return UnavailableClientRegistry()
