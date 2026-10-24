"""Provide persistent client and API key registry access."""

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
        """Look up an active gateway key and its active client identity.

        :param key_id: Public identifier embedded in a structured gateway API
            key.
        :returns: Matching ClientKeyRecord, or None when no active key exists.

        .. note::

            - Precondition: key_id is a validated public gateway key
              identifier.
            - Side effect: Registry-specific connection or query state, if any.
        """
        ...


class EnvironmentClientRegistry:
    """
    Legacy/test registry backed by SAG_CLIENTS rather than persistent storage.
    """

    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """Parse the environment-backed registry for deterministic tests.

        :param key_id: Public key identifier to look up.
        :returns: Matching ClientKeyRecord, or None when the key ID is unknown.

        .. note::

            - Precondition: SAG_CLIENTS may contain serialized hashed
              client-key records.
            - Fails closed when the registry is missing or malformed.
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
        """Create a closed async PostgreSQL connection pool.

        :param database_url: PostgreSQL connection string.
        :param min_size: Minimum number of pooled connections.
        :param max_size: Maximum number of pooled connections.
        :returns: A configured PostgresClientRegistry instance.

        .. note::

            - Precondition: database_url is a PostgreSQL connection string.
            - Precondition: min_size and max_size define a valid
              connection-pool range.
            - Side effect: Initializes process-local PostgreSQL pool state.
            - Defers network connections until the first registry query.
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
                raise ClientRegistryUnavailable(
                    "client_registry_unavailable"
                ) from exc

            self._is_open = True

    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """Open the pool on first use.

        :param key_id: Public key identifier to look up.
        :returns: Matching ClientKeyRecord, or None when no active key exists.

        .. note::

            - Precondition: key_id is a validated public gateway key
              identifier.
            - Precondition: Database schema for gateway_clients and
              gateway_api_keys has been initialized.
            - Side effect: PostgreSQL connection-pool state.
            - Queries only active keys belonging to active clients.
            - Converts database failures into a non-secret registry error.
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
            raise ClientRegistryUnavailable(
                "client_registry_unavailable"
            ) from exc

        if row is None:
            return None

        client_id, resolved_key_id, api_key_sha256 = row
        return ClientKeyRecord(
            client_id=str(client_id),
            key_id=str(resolved_key_id),
            api_key_sha256=str(api_key_sha256),
        )

    async def close(self) -> None:
        """Close pooled database connections when the pool was opened.

        .. note::

            - Precondition: The registry may have opened its process-local
              connection pool.
            - Side effect: PostgreSQL connection-pool state.
        """
        if self._is_open:
            await self._pool.close()
            self._is_open = False


class UnavailableClientRegistry:
    async def get_key_record(self, key_id: str) -> ClientKeyRecord | None:
        """Fail closed when no usable registry backend is configured.

        :param key_id: Function input.
        :returns: A value matching the declared ClientKeyRecord | None return
            contract.

        .. note::

            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
        """
        raise ClientRegistryUnavailable("client_registry_not_configured")


def build_client_registry() -> ClientRegistry:
    """Select persistent PostgreSQL storage by default.

    :returns: Configured ClientRegistry implementation.

    .. note::

        - Precondition: SAG_CLIENT_REGISTRY_BACKEND may select postgres or
          environment.
        - Precondition: DATABASE_URL is configured when the postgres backend is
          selected.
        - Side effect: Initializes registry object state.
        - Retains an explicit environment backend for deterministic tests only.
        - Fails closed through UnavailableClientRegistry for unusable
          configuration.
    """
    backend = (
        os.getenv("SAG_CLIENT_REGISTRY_BACKEND", "postgres").strip().lower()
    )

    if backend == "environment":
        return EnvironmentClientRegistry()

    if backend == "postgres":
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            return PostgresClientRegistry(database_url)
        return UnavailableClientRegistry()

    return UnavailableClientRegistry()
