import argparse
import asyncio
import os
from pathlib import Path
from typing import TextIO

import psycopg
from psycopg.errors import UniqueViolation

from app.api_keys import (
    ClientKeyRecord,
    generate_api_key,
    managed_api_key_secret_file,
    parse_client_records,
    parse_key_id,
    write_api_key_secret,
)

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
    secret_file: TextIO | None = None,
) -> str:
    """
    RME

    Requires:
        - database_url identifies an initialized PostgreSQL database.
        - client_id is valid for gateway identity generation.
        - secret_file, when provided, is an exclusive owner-only writable stream.

    Modifies:
        - gateway_clients and gateway_api_keys database tables.
        - Operating-system cryptographic random state.
        - secret_file contents when a stream is provided.

    Effects:
        - Generates a new high-entropy gateway key.
        - Writes the candidate raw key to the secret stream before persistence when provided.
        - Persists only its SHA-256 digest and public key metadata.
        - Rewrites the secret stream if an extremely unlikely key-ID collision requires retry.

    Inputs:
        - database_url: PostgreSQL connection string.
        - client_id: Stable gateway client identity.
        - secret_file: Optional secure one-time credential delivery stream.

    Outputs:
        - Raw API key for programmatic callers.
    """
    for _ in range(3):
        api_key, record = generate_api_key(client_id)
        if secret_file is not None:
            write_api_key_secret(secret_file, api_key)
        try:
            await insert_client_key_record(database_url, record)
        except ValueError as exc:
            if str(exc) == "key_id_collision":
                continue
            raise
        return api_key

    raise RuntimeError("unable_to_generate_unique_key_id")


async def revoke_client_key(database_url: str, key_id: str) -> bool:
    """
    RME

    Requires:
        - database_url identifies an initialized PostgreSQL database.
        - key_id identifies a stored gateway API key.

    Modifies:
        - gateway_api_keys.is_active and gateway_api_keys.revoked_at.

    Effects:
        - Revokes the key immediately for subsequent authentication lookups.
        - Is idempotent when the key has already been revoked.
        - Rejects unknown key IDs.

    Inputs:
        - database_url: PostgreSQL connection string.
        - key_id: Public gateway key identifier to revoke.

    Outputs:
        - True when this call changed an active key to revoked; False when already revoked.
    """
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE gateway_api_keys
                SET is_active = FALSE,
                    revoked_at = COALESCE(revoked_at, NOW())
                WHERE key_id = %s
                  AND is_active = TRUE
                RETURNING client_id
                """,
                (key_id,),
            )
            revoked = await cursor.fetchone()
            if revoked is not None:
                return True

            await cursor.execute(
                "SELECT 1 FROM gateway_api_keys WHERE key_id = %s",
                (key_id,),
            )
            exists = await cursor.fetchone()

    if exists is None:
        raise ValueError("unknown_key_id")
    return False


async def rotate_client_keys(
    database_url: str,
    client_id: str,
    secret_file: TextIO | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    """
    RME

    Requires:
        - database_url identifies an initialized PostgreSQL database.
        - client_id identifies an active client with at least one active API key.
        - secret_file, when provided, is an exclusive owner-only writable stream.

    Modifies:
        - gateway_api_keys rows for the client.
        - Operating-system cryptographic random state.
        - secret_file contents when a stream is provided.

    Effects:
        - Creates one replacement API key and stores only its hash.
        - Writes and synchronizes the replacement secret before revoking existing keys.
        - Revokes all previously active keys for the client in the same transaction.
        - Rolls back the full rotation when secret delivery or any database step fails.

    Inputs:
        - database_url: PostgreSQL connection string.
        - client_id: Client identity whose active keys are being rotated.
        - secret_file: Optional secure one-time credential delivery stream.

    Outputs:
        - Tuple containing the new raw API key, new key ID, and revoked key IDs.
    """
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        async with connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT is_active
                    FROM gateway_clients
                    WHERE client_id = %s
                    FOR UPDATE
                    """,
                    (client_id,),
                )
                client_row = await cursor.fetchone()
                if client_row is None:
                    raise ValueError("unknown_client_id")
                if not bool(client_row[0]):
                    raise ValueError("client_inactive")

                await cursor.execute(
                    """
                    SELECT key_id
                    FROM gateway_api_keys
                    WHERE client_id = %s
                      AND is_active = TRUE
                    ORDER BY created_at, key_id
                    """,
                    (client_id,),
                )
                active_rows = await cursor.fetchall()
                if not active_rows:
                    raise ValueError("no_active_keys_to_rotate")

                new_api_key: str | None = None
                new_key_id: str | None = None
                for _ in range(3):
                    candidate_api_key, candidate_record = generate_api_key(client_id)
                    await cursor.execute(
                        """
                        INSERT INTO gateway_api_keys (
                            key_id,
                            client_id,
                            api_key_sha256
                        )
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING key_id
                        """,
                        (
                            candidate_record.key_id,
                            candidate_record.client_id,
                            candidate_record.api_key_sha256,
                        ),
                    )
                    inserted = await cursor.fetchone()
                    if inserted is not None:
                        new_api_key = candidate_api_key
                        new_key_id = candidate_record.key_id
                        break

                if new_api_key is None or new_key_id is None:
                    raise RuntimeError("unable_to_generate_unique_key_id")

                if secret_file is not None:
                    write_api_key_secret(secret_file, new_api_key)

                await cursor.execute(
                    """
                    UPDATE gateway_api_keys
                    SET is_active = FALSE,
                        revoked_at = COALESCE(revoked_at, NOW())
                    WHERE client_id = %s
                      AND key_id <> %s
                      AND is_active = TRUE
                    RETURNING key_id
                    """,
                    (client_id, new_key_id),
                )
                revoked_rows = await cursor.fetchall()

    revoked_key_ids = tuple(str(row[0]) for row in revoked_rows)
    return new_api_key, new_key_id, revoked_key_ids


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
    """
    RME

    Requires:
        - args contains one supported client-registry subcommand.
        - DATABASE_URL identifies the registry database.
        - create/rotate commands provide a new --api-key-file path.

    Modifies:
        - PostgreSQL client/key state according to the selected command.
        - A 0600 API-key delivery file for create/rotate commands.
        - Standard output with non-secret command metadata.

    Effects:
        - Executes the selected administrative command.
        - Never writes raw API keys to standard output.
        - Removes incomplete API-key output files when create/rotate fails.

    Inputs:
        - args: Parsed command-line namespace.

    Outputs:
        - Human-readable non-secret command status on standard output.
        - Raw API key in the requested secret file for create/rotate.
    """
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
        with managed_api_key_secret_file(args.api_key_file) as secret_file:
            api_key = await create_client_key(
                database_url,
                args.client_id,
                secret_file,
            )
        key_id = parse_key_id(api_key)
        if key_id is None:
            raise RuntimeError("generated_invalid_api_key")
        print(f"Client ID: {args.client_id}")
        # key_id is intentionally public metadata; only the trailing token is secret.
        # codeql[py/clear-text-logging-sensitive-data]
        print(f"Key ID: {key_id}")
        print(f"API key file: {args.api_key_file}")
        print("Store the secret file securely and delete it after client provisioning.")
        return

    if args.command == "revoke":
        changed = await revoke_client_key(database_url, args.key_id)
        if changed:
            print(f"Revoked key_id={args.key_id}.")
        else:
            print(f"Key key_id={args.key_id} was already revoked.")
        return

    if args.command == "rotate":
        with managed_api_key_secret_file(args.api_key_file) as secret_file:
            _, key_id, revoked_key_ids = await rotate_client_keys(
                database_url,
                args.client_id,
                secret_file,
            )
        print(f"Client ID: {args.client_id}")
        print(f"New key ID: {key_id}")
        print(f"API key file: {args.api_key_file}")
        print("Revoked key IDs: " + ",".join(revoked_key_ids))
        print("Store the secret file securely and delete it after client provisioning.")
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
        - Owner-only API-key output files for create/rotate commands.
        - Terminal output containing non-secret metadata only.

    Effects:
        - Initializes schema, migrates records, creates keys, revokes keys, rotates keys,
          or lists non-secret metadata.
        - Requires explicit secret-file delivery for newly generated raw credentials.

    Inputs:
        - Command-line subcommand and arguments.

    Outputs:
        - Human-readable non-secret command result written to standard output.
        - Raw API keys only in explicitly requested 0600 output files.
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
    create_parser.add_argument(
        "--api-key-file",
        required=True,
        help="New owner-only file that will receive the raw API key.",
    )

    revoke_parser = subparsers.add_parser(
        "revoke",
        help="Revoke one API key by public key ID.",
    )
    revoke_parser.add_argument("key_id")

    rotate_parser = subparsers.add_parser(
        "rotate",
        help="Create a replacement key and atomically revoke prior active keys.",
    )
    rotate_parser.add_argument("client_id")
    rotate_parser.add_argument(
        "--api-key-file",
        required=True,
        help="New owner-only file that will receive the replacement API key.",
    )

    subparsers.add_parser("list", help="List non-secret client/key metadata.")

    args = parser.parse_args()
    try:
        asyncio.run(_run_command(args))
    except (ValueError, RuntimeError, OSError, psycopg.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()