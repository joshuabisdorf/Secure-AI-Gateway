"""Resolve AWS secrets and managed-service configuration."""

import argparse
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import boto3
from psycopg.conninfo import make_conninfo

_database_user_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_provider_secret_fields = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
    }
)


class SecretsManagerClient(Protocol):
    def get_secret_value(self, **kwargs: Any) -> Mapping[str, Any]: ...


class RuntimeSecretError(RuntimeError):
    """
    Raised with a safe reason when cloud runtime secret loading fails closed.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _secrets_client() -> SecretsManagerClient:
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    return boto3.client("secretsmanager", region_name=region)


def load_json_secret(
    secret_id: str,
    *,
    client: SecretsManagerClient | None = None,
) -> dict[str, Any]:
    """Retrieve one secret through the AWS default credential chain.

    :param secret_id: Secret ARN or name.
    :param client: Optional injected Secrets Manager client for deterministic
        tests.
    :returns: Parsed JSON object.

    .. note::

        - Precondition: secret_id identifies a Secrets Manager secret
          containing a JSON object in SecretString.
        - Precondition: The current AWS identity has least-privilege access to
          the requested secret.
        - Side effect: Short-lived AWS SDK request/session state only.
        - Rejects binary, malformed, or non-object secret values without
          exposing contents.
    """
    if not secret_id or not secret_id.strip():
        raise RuntimeSecretError("secret_id_not_configured")

    try:
        response = (client or _secrets_client()).get_secret_value(
            SecretId=secret_id
        )
    except Exception as exc:
        raise RuntimeSecretError("secret_unavailable") from exc

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise RuntimeSecretError("secret_string_required")
    try:
        parsed = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise RuntimeSecretError("secret_json_invalid") from exc
    if not isinstance(parsed, dict):
        raise RuntimeSecretError("secret_json_object_required")
    return parsed


def _database_credentials(value: Mapping[str, Any]) -> tuple[str, str]:
    """Validate the exact credential schema, username, and password length.

    :param value: Candidate runtime database credentials.
    :returns: Validated username and password.

    .. note::

        - Precondition: value is the parsed runtime database secret object.
        - Rejects malformed credentials without exposing their values.
    """
    if frozenset(value) != {"username", "password"}:
        raise RuntimeSecretError("database_secret_schema_invalid")
    username = value.get("username")
    password = value.get("password")
    if not isinstance(username, str) or not _database_user_pattern.fullmatch(
        username
    ):
        raise RuntimeSecretError("database_username_invalid")
    if not isinstance(password, str) or len(password.encode("utf-8")) < 32:
        raise RuntimeSecretError("database_password_invalid")
    return username, password


def build_database_conninfo(
    credentials: Mapping[str, Any],
    *,
    host: str,
    port: str,
    database: str,
) -> str:
    """Produce a libpq connection string with TLS required.

    :param credentials: Runtime database secret JSON object.
    :param host: Database hostname.
    :param port: Database port.
    :param database: Database name.
    :returns: PostgreSQL connection string suitable for DATABASE_URL.

    .. note::

        - Precondition: credentials contains only a validated runtime
          username/password pair.
        - Precondition: host, port, and database identify the private RDS
          database endpoint.
        - Escapes connection fields using psycopg rather than string
          concatenation.
    """
    username, password = _database_credentials(credentials)
    if not host or not host.strip():
        raise RuntimeSecretError("database_host_not_configured")
    try:
        parsed_port = int(port)
    except (TypeError, ValueError) as exc:
        raise RuntimeSecretError("database_port_invalid") from exc
    if parsed_port < 1 or parsed_port > 65535:
        raise RuntimeSecretError("database_port_invalid")
    if not database or not database.strip():
        raise RuntimeSecretError("database_name_not_configured")

    return make_conninfo(
        host=host.strip(),
        port=parsed_port,
        dbname=database.strip(),
        user=username,
        password=password,
        sslmode="require",
    )


def _tool_signing_key(value: Mapping[str, Any]) -> str:
    """Validate exact schema and safe signing-key byte-length bounds.

    :param value: Candidate signing-key secret object.
    :returns: Validated execution-ticket signing key.

    .. note::

        - Precondition: value is the parsed execution-ticket signing secret
          object.
        - Fails closed without logging or returning malformed secret material.
    """
    if frozenset(value) != {"signing_key"}:
        raise RuntimeSecretError("tool_signing_secret_schema_invalid")
    signing_key = value.get("signing_key")
    if not isinstance(signing_key, str):
        raise RuntimeSecretError("tool_signing_key_invalid")
    size = len(signing_key.encode("utf-8"))
    if size < 32 or size > 1024:
        raise RuntimeSecretError("tool_signing_key_invalid")
    return signing_key


def _provider_environment(value: Mapping[str, Any]) -> dict[str, str]:
    """Allow only the explicitly supported provider environment fields.

    :param value: Candidate provider-secret mapping.
    :returns: Validated provider environment additions.

    .. note::

        - Precondition: value is the parsed optional provider secret object.
        - Rejects non-string or unexpected fields without exposing values.
    """
    if not frozenset(value).issubset(_provider_secret_fields):
        raise RuntimeSecretError("provider_secret_schema_invalid")
    environment: dict[str, str] = {}
    for key, raw_value in value.items():
        if not isinstance(raw_value, str):
            raise RuntimeSecretError("provider_secret_schema_invalid")
        environment[key] = raw_value
    return environment


def load_gateway_runtime_environment(
    *,
    client: SecretsManagerClient | None = None,
) -> dict[str, str]:
    """Resolve database credentials and execution-ticket signing material from
        Secrets
    Manager.

    :param client: Optional injected Secrets Manager client for deterministic
        tests.
    :returns: Environment variables required by the gateway child process.

    .. note::

        - Precondition: Cloud runtime non-secret endpoint/secret-ID environment
          is configured.
        - Precondition: The Pod Identity role can read only the gateway runtime
          secret containers.
        - Side effect: Nothing; returns environment additions for the child
          gateway process.
        - Optionally resolves provider credentials when a provider-secret ID is
          configured.
        - Never returns AWS credentials or logs secret values.
    """
    database_secret_id = os.getenv("SAG_AWS_DATABASE_SECRET_ID", "")
    signing_secret_id = os.getenv("SAG_AWS_TOOL_SIGNING_SECRET_ID", "")
    provider_secret_id = os.getenv("SAG_AWS_PROVIDER_SECRET_ID", "").strip()

    database_secret = load_json_secret(database_secret_id, client=client)
    signing_secret = load_json_secret(signing_secret_id, client=client)

    environment = {
        "DATABASE_URL": build_database_conninfo(
            database_secret,
            host=os.getenv("SAG_DATABASE_HOST", ""),
            port=os.getenv("SAG_DATABASE_PORT", "5432"),
            database=os.getenv("SAG_DATABASE_NAME", ""),
        ),
        "SAG_TOOL_EXECUTION_SIGNING_KEY": _tool_signing_key(signing_secret),
    }

    if provider_secret_id:
        provider_secret = load_json_secret(provider_secret_id, client=client)
        environment.update(_provider_environment(provider_secret))

    return environment


def exec_with_runtime_environment(command: Sequence[str]) -> None:
    """Load runtime secrets into an in-memory child environment only.

    :param command: Executable and arguments to run.
    :returns: None; successful execution replaces the current process.

    .. note::

        - Precondition: command contains an executable and arguments.
        - Precondition: Cloud runtime secret configuration is valid.
        - Side effect: Replaces the current process with the requested command.
        - Does not write secret values to files or standard output.
    """
    if not command:
        raise RuntimeSecretError("runtime_command_required")
    environment = os.environ.copy()
    environment.update(load_gateway_runtime_environment())
    os.execvpe(command[0], list(command), environment)


def main() -> None:
    """Load cloud runtime secrets and execs the gateway command.

    :returns: None; successful execution replaces the current process.

    .. note::

        - Input: Command-line arguments and AWS runtime environment
          configuration.
        - Precondition: CLI arguments contain the gateway command after
          optional `--`.
        - Precondition: Required AWS runtime secret configuration is available.
        - Side effect: Process state when the runtime command is executed.
        - Converts safe RuntimeSecretError reasons into CLI parser errors.
        - Does not print secret values.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Load Secure AI Gateway AWS runtime secrets and exec a command."
        )
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    try:
        exec_with_runtime_environment(command)
    except RuntimeSecretError as exc:
        parser.error(f"runtime secret configuration failed: {exc.reason}")


if __name__ == "__main__":
    main()
