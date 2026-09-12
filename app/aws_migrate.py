import asyncio
import os
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.aws_runtime import RuntimeSecretError, load_json_secret
from app.database import migrate_database


def _required_text(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeSecretError(reason)
    return value


def _admin_credentials(value: Mapping[str, Any]) -> tuple[str, str]:
    username = _required_text(value.get("username"), "rds_admin_username_invalid")
    password = _required_text(value.get("password"), "rds_admin_password_invalid")
    return username, password


def _runtime_credentials(value: Mapping[str, Any]) -> tuple[str, str]:
    if frozenset(value) != {"username", "password"}:
        raise RuntimeSecretError("database_secret_schema_invalid")
    username = _required_text(value.get("username"), "database_username_invalid")
    password = _required_text(value.get("password"), "database_password_invalid")
    if len(password.encode("utf-8")) < 32:
        raise RuntimeSecretError("database_password_invalid")
    return username, password


def _database_coordinates() -> tuple[str, int, str]:
    host = os.getenv("SAG_DATABASE_HOST", "").strip()
    database = os.getenv("SAG_DATABASE_NAME", "").strip()
    try:
        port = int(os.getenv("SAG_DATABASE_PORT", "5432"))
    except ValueError as exc:
        raise RuntimeSecretError("database_port_invalid") from exc
    if not host:
        raise RuntimeSecretError("database_host_not_configured")
    if not database:
        raise RuntimeSecretError("database_name_not_configured")
    if port < 1 or port > 65535:
        raise RuntimeSecretError("database_port_invalid")
    return host, port, database


def _conninfo(username: str, password: str) -> str:
    host, port, database = _database_coordinates()
    return make_conninfo(
        host=host,
        port=port,
        dbname=database,
        user=username,
        password=password,
        sslmode="require",
    )


async def provision_runtime_role(
    admin_conninfo: str,
    *,
    admin_username: str,
    runtime_username: str,
    runtime_password: str,
) -> None:
    """
    RME

    Requires:
        - admin_conninfo authenticates as the RDS administrative database role over TLS.
        - Database migrations have already created the gateway runtime tables.
        - runtime_username/password are generated deployment credentials.

    Modifies:
        - PostgreSQL role metadata and grants for the gateway runtime login.

    Effects:
        - Creates or rotates the gateway login without granting DDL/admin capabilities.
        - Grants CONNECT, schema USAGE, DML on current tables, and sequence use.
        - Configures equivalent default privileges for future objects created by the migration role.

    Inputs:
        - admin_conninfo: Administrative PostgreSQL connection string.
        - admin_username: RDS migration-owner role name.
        - runtime_username: Least-privilege gateway role name.
        - runtime_password: Gateway role password.

    Outputs:
        - None.
    """
    _, _, database = _database_coordinates()
    runtime_identifier = sql.Identifier(runtime_username)
    runtime_password_literal = sql.Literal(runtime_password)
    admin_identifier = sql.Identifier(admin_username)
    database_identifier = sql.Identifier(database)

    async with await psycopg.AsyncConnection.connect(admin_conninfo) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (runtime_username,),
            )
            exists = await cursor.fetchone()

        if exists is None:
            await connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(runtime_identifier, runtime_password_literal)
            )
        else:
            await connection.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(runtime_identifier, runtime_password_literal)
            )

        await connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                database_identifier,
                runtime_identifier,
            )
        )
        await connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(runtime_identifier)
        )
        await connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(runtime_identifier)
        )
        await connection.execute(
            sql.SQL(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(runtime_identifier)
        )
        await connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(admin_identifier, runtime_identifier)
        )
        await connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
            ).format(admin_identifier, runtime_identifier)
        )
        await connection.commit()


async def migrate_aws_database() -> tuple[str, ...]:
    """
    RME

    Requires:
        - Migration Pod Identity can read the RDS-managed admin secret and runtime DB secret.
        - Private RDS endpoint coordinates are supplied as non-secret environment variables.

    Modifies:
        - PostgreSQL schema, migration metadata, runtime role password, and grants.

    Effects:
        - Applies versioned schema migrations using the administrative role.
        - Creates/rotates a least-privilege runtime login for gateway pods.
        - Does not expose either secret value to standard output.

    Inputs:
        - None.

    Outputs:
        - Tuple of migration filenames applied by this invocation.
    """
    admin_secret_id = os.getenv("SAG_AWS_RDS_ADMIN_SECRET_ID", "")
    runtime_secret_id = os.getenv("SAG_AWS_DATABASE_SECRET_ID", "")
    admin_secret = load_json_secret(admin_secret_id)
    runtime_secret = load_json_secret(runtime_secret_id)

    admin_username, admin_password = _admin_credentials(admin_secret)
    runtime_username, runtime_password = _runtime_credentials(runtime_secret)
    if runtime_username == admin_username:
        raise RuntimeSecretError("runtime_database_role_must_differ_from_admin")

    admin_conninfo = _conninfo(admin_username, admin_password)
    applied = await migrate_database(admin_conninfo)
    await provision_runtime_role(
        admin_conninfo,
        admin_username=admin_username,
        runtime_username=runtime_username,
        runtime_password=runtime_password,
    )
    return applied


def main() -> None:
    """
    RME

    Requires:
        - AWS migration environment and Pod Identity are configured.

    Modifies:
        - RDS schema and least-privilege runtime role.
        - Terminal output containing metadata only.

    Effects:
        - Applies cloud database migrations and runtime-role grants.
        - Exits nonzero with a safe reason when secret/configuration loading fails.

    Inputs:
        - None.

    Outputs:
        - Safe migration status without credentials or connection strings.
    """
    try:
        applied = asyncio.run(migrate_aws_database())
    except RuntimeSecretError as exc:
        raise SystemExit(f"AWS migration configuration failed: {exc.reason}") from exc
    if applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("Database schema is up to date.")
    print("Gateway runtime database role is configured.")


if __name__ == "__main__":
    main()
