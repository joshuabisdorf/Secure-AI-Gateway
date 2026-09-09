import argparse
import asyncio
import os
from pathlib import Path

import psycopg
from psycopg.errors import UniqueViolation

from app.api_keys import ClientKeyRecord, generate_api_key, parse_client_records

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "001_client_registry.sql"
)


def get_database_url() -> str:
    """
    RME

    Requires:
        - DATABASE_URL may contain the PostgreSQL connection string.

    Modifies:
        - Nothing.

    Effects:
        - Fails when persistent database configuration is missing.

    Inputs:
        - None.

    Outputs:
        - Configured PostgreSQL connection string.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is not configured")
    return database_url


async def initialize_database(database_url: str) -> None:
    """
    RME

    Requires:
        - database_url identifies a reachable PostgreSQL database.
        - The client-registry migration file exists in the repository.

    Modifies:
        - PostgreSQL schema.

    Effects:
        - Creates gateway_clients, gateway_api_keys, and supporting indexes.
        - Applies idempotent schema statements in migration order.

    Inputs:
        - database_url: PostgreSQL connection string.

    Outputs:
        - None.
    """
    migration_sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    statements = [
        statement.strip()
        for statement in migration_sql.split(";")
        if statement.strip()
    ]

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        for statement in statements:
            await connection.execute(statement)


async def insert_client_key_record(
    database_url: str,
    record: ClientKeyRecord,
) -> None:
    """
    RME

    Requires:
        - database_url identifies an initialized PostgreSQL database.
        - record contains a validated client ID, key ID, and SHA-256 digest.

    Modifies:
        - gateway_clients and gateway_api_keys database tables.

    Effects:
        - Creates the client when it does not already exist.
        - Inserts the hashed key record without storing the raw credential.
        - Treats an identical existing key record as idempotent.
        - Rejects a key-ID collision with different stored identity/hash data.

    Inputs:
        - database_url: PostgreSQL connection string.
        - record: Hashed gateway client-key record.

    Outputs:
        - None.
    """
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        await connection.execute(
            """
            INSERT INTO gateway_clients (client_id)
            VALUES (%s)
            ON CONFLICT (client_id) DO NOTHING
            """,
            (record.client_id,),
        )

        try:
            await connection.execute(
                """
                INSERT INTO gateway_api_keys (
                    key_id,
                    client_id,
                    api_key_sha256
                )
                VALUES (%s, %s, %s)
                """,
                (record.key_id, record.client_id, record.api_key_sha256),
            )
            return
        except UniqueViolation:
            await connection.rollback()

        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT client_id, api_key_sha256
                    FROM gateway_api_keys
                    WHERE key_id = %s
                    """,
                    (record.key_id,),
                )
                existing = await cursor.fetchone()

        if existing == (record.client_id, record.api_key_sha256):
            return

        raise ValueError("key_id_collision")


async def create_client_key(
    database_url: str,
    client_id: str,
) -> str:
    """
    RME

    Requires:
        - database_url identifies an initialized PostgreSQL database.
        - client_id is valid for gateway identity generation.

    Modifies:
        - gateway_clients and gateway_api_keys database tables.
        - Operating-system cryptographic random state.

    Effects:
        - Generates a new high-entropy gateway key.
        - Persists only its SHA-256 digest and public key metadata.

    Inputs:
        - database_url: PostgreSQL connection string.
        - client_id: Stable gateway client identity.

    Outputs:
        - Raw API key to deliver to the client exactly once.
    """
    for _ in range(3):
        api_key, record = generate_api_key(client_id)
        try:
            await insert_client_key_record(database_url, record)
        except ValueError as exc:
            if str(exc) == "key_id_collision":
                continue
            raise
        return api_key

    raise RuntimeError("unable_to_generate_unique_key_id")


async def import_environment_records(database_url: str) -> int:
    """
    RME

    Requires:
        - SAG_CLIENTS contains the previous environment-backed hashed registry.
        - database_url identifies an initialized PostgreSQL database.

    Modifies:
        - gateway_clients and gateway_api_keys database tables.

    Effects:
        - Migrates existing hashed client records without requiring raw API keys.

    Inputs:
        - database_url: PostgreSQL connection string.

    Outputs:
        - Number of client-key records imported or confirmed present.
    """
    configured_clients = os.getenv("SAG_CLIENTS")
    if not configured_clients:
        raise ValueError("SAG_CLIENTS is not configured")

    records = parse_client_records(configured_clients)
    for record in records.values():
        await insert_client_key_record(database_url, record)

    return len(records)


async def list_client_keys(database_url: str) -> list[tuple[str, str, bool, bool]]:
    """
    RME

    Requires:
        - database_url identifies an initialized PostgreSQL database.

    Modifies:
        - PostgreSQL query/session state only.

    Effects:
        - Reads non-secret client and key metadata.

    Inputs:
        - database_url: PostgreSQL connection string.

    Outputs:
        - Tuples of client_id, key_id, client_active, and key_active.
    """
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT c.client_id, k.key_id, c.is_active, k.is_active
                FROM gateway_clients AS c
                JOIN gateway_api_keys AS k
                  ON k.client_id = c.client_id
                ORDER BY c.client_id, k.created_at, k.key_id
                """
            )
            rows = await cursor.fetchall()

    return [
        (str(client_id), str(key_id), bool(client_active), bool(key_active))
        for client_id, key_id, client_active, key_active in rows
    ]


async def _run_command(args: argparse.Namespace) -> None:
    database_url = get_database_url()

    if args.command == "init-db":
        await initialize_database(database_url)
        print("Client registry schema initialized.")
        return

    if args.command == "import-env":
        imported = await import_environment_records(database_url)
        print(f"Imported {imported} client key record(s).")
        return

    if args.command == "create":
        api_key = await create_client_key(database_url, args.client_id)
        print(f"Client ID: {args.client_id}")
        print(f"API key: {api_key}")
        print("Store this raw key on the client; it is not stored in PostgreSQL.")
        return

    if args.command == "list":
        rows = await list_client_keys(database_url)
        if not rows:
            print("No client keys found.")
            return

        for client_id, key_id, client_active, key_active in rows:
            print(
                f"client_id={client_id} key_id={key_id} "
                f"client_active={str(client_active).lower()} "
                f"key_active={str(key_active).lower()}"
            )
        return

    raise ValueError("unsupported_command")


def main() -> None:
    """
    RME

    Requires:
        - DATABASE_URL is available in the process environment.

    Modifies:
        - PostgreSQL client-registry state for write commands.
        - Terminal output.

    Effects:
        - Initializes schema, migrates environment records, creates keys, or lists metadata.

    Inputs:
        - Command-line subcommand and arguments.

    Outputs:
        - Human-readable command result written to standard output.
    """
    parser = argparse.ArgumentParser(
        description="Manage Secure AI Gateway PostgreSQL client identities."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize the client registry schema.")
    subparsers.add_parser(
        "import-env",
        help="Import existing hashed SAG_CLIENTS records into PostgreSQL.",
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Create a client identity/key and persist only the key hash.",
    )
    create_parser.add_argument("client_id")

    subparsers.add_parser("list", help="List non-secret client/key metadata.")

    args = parser.parse_args()
    try:
        asyncio.run(_run_command(args))
    except (ValueError, OSError, psycopg.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
