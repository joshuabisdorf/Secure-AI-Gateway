import argparse
import asyncio
import hashlib
import os
from pathlib import Path

import psycopg

_MIGRATION_DIRECTORY = Path(__file__).resolve().parents[1] / "db" / "migrations"


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


def _migration_files() -> list[Path]:
    files = sorted(_MIGRATION_DIRECTORY.glob("*.sql"))
    if not files:
        raise ValueError("no_database_migrations_found")
    return files


async def migrate_database(database_url: str) -> tuple[str, ...]:
    """
    RME

    Requires:
        - database_url identifies a reachable PostgreSQL database.
        - Migration files are ordered lexicographically in db/migrations.

    Modifies:
        - PostgreSQL schema and schema_migrations metadata.

    Effects:
        - Creates migration tracking when absent.
        - Applies each unapplied migration exactly once in filename order.
        - Refuses to continue if an already-applied migration file was modified.

    Inputs:
        - database_url: PostgreSQL connection string.

    Outputs:
        - Tuple containing filenames applied by this invocation.
    """
    applied_now: list[str] = []

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                sha256 CHAR(64) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        for migration_path in _migration_files():
            sql = migration_path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()

            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT sha256 FROM schema_migrations WHERE filename = %s",
                    (migration_path.name,),
                )
                row = await cursor.fetchone()

            if row is not None:
                if str(row[0]) != digest:
                    raise ValueError(
                        f"applied migration changed: {migration_path.name}"
                    )
                continue

            statements = [
                statement.strip()
                for statement in sql.split(";")
                if statement.strip()
            ]
            async with connection.transaction():
                for statement in statements:
                    await connection.execute(statement)
                await connection.execute(
                    """
                    INSERT INTO schema_migrations (filename, sha256)
                    VALUES (%s, %s)
                    """,
                    (migration_path.name, digest),
                )

            applied_now.append(migration_path.name)

    return tuple(applied_now)


async def list_migrations(database_url: str) -> list[tuple[str, str]]:
    """
    RME

    Requires:
        - database_url identifies a reachable PostgreSQL database.

    Modifies:
        - PostgreSQL query/session state only.

    Effects:
        - Reads applied migration metadata when the tracking table exists.

    Inputs:
        - database_url: PostgreSQL connection string.

    Outputs:
        - Applied migration filename and timestamp strings.
    """
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT filename, applied_at
                FROM schema_migrations
                ORDER BY filename
                """
            )
            rows = await cursor.fetchall()

    return [(str(filename), str(applied_at)) for filename, applied_at in rows]


async def _run_command(args: argparse.Namespace) -> None:
    database_url = get_database_url()
    if args.command == "migrate":
        applied = await migrate_database(database_url)
        if applied:
            print("Applied migrations: " + ", ".join(applied))
        else:
            print("Database schema is up to date.")
        return

    if args.command == "status":
        rows = await list_migrations(database_url)
        if not rows:
            print("No migrations recorded.")
            return
        for filename, applied_at in rows:
            print(f"filename={filename} applied_at={applied_at}")
        return

    raise ValueError("unsupported_command")


def main() -> None:
    """
    RME

    Requires:
        - DATABASE_URL is available in the process environment.

    Modifies:
        - PostgreSQL schema for the migrate command.
        - Terminal output.

    Effects:
        - Applies or displays ordered database migrations.

    Inputs:
        - Command-line subcommand.

    Outputs:
        - Human-readable migration result written to standard output.
    """
    parser = argparse.ArgumentParser(
        description="Manage Secure AI Gateway PostgreSQL schema migrations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply pending database migrations.")
    subparsers.add_parser("status", help="List recorded database migrations.")

    args = parser.parse_args()
    try:
        asyncio.run(_run_command(args))
    except (ValueError, OSError, psycopg.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
