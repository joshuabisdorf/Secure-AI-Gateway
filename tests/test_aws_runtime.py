import json

import pytest

from app.aws_runtime import (
    RuntimeSecretError,
    build_database_conninfo,
    load_gateway_runtime_environment,
    load_json_secret,
)


class _FakeSecretsManager:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values
        self.requested: list[str] = []

    def get_secret_value(self, **kwargs: object) -> dict[str, object]:
        secret_id = str(kwargs["SecretId"])
        self.requested.append(secret_id)
        value = self.values[secret_id]
        return {"SecretString": json.dumps(value)}


def test_load_gateway_runtime_environment(monkeypatch) -> None:
    """
    RME

    Requires:
        - Runtime secret IDs and private database coordinates are configured.

    Modifies:
        - Test-only environment variables and fake secret-request history.

    Effects:
        - Verifies database/signing/provider secrets are mapped to child environment values.
        - Verifies generated database connection configuration requires TLS.

    Inputs:
        - monkeypatch: pytest environment fixture.

    Outputs:
        - None. Assertions determine cloud runtime secret behavior.
    """
    monkeypatch.setenv("SAG_AWS_DATABASE_SECRET_ID", "database")
    monkeypatch.setenv("SAG_AWS_TOOL_SIGNING_SECRET_ID", "signing")
    monkeypatch.setenv("SAG_AWS_PROVIDER_SECRET_ID", "provider")
    monkeypatch.setenv("SAG_DATABASE_HOST", "db.internal.example")
    monkeypatch.setenv("SAG_DATABASE_PORT", "5432")
    monkeypatch.setenv("SAG_DATABASE_NAME", "secure_ai_gateway")

    fake = _FakeSecretsManager(
        {
            "database": {
                "username": "sag_runtime",
                "password": "x" * 48,
            },
            "signing": {"signing_key": "y" * 48},
            "provider": {
                "OPENROUTER_API_KEY": "provider-secret-value",
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            },
        }
    )

    environment = load_gateway_runtime_environment(client=fake)

    assert "host=db.internal.example" in environment["DATABASE_URL"]
    assert "dbname=secure_ai_gateway" in environment["DATABASE_URL"]
    assert "user=sag_runtime" in environment["DATABASE_URL"]
    assert "sslmode=require" in environment["DATABASE_URL"]
    assert environment["SAG_TOOL_EXECUTION_SIGNING_KEY"] == "y" * 48
    assert environment["OPENROUTER_API_KEY"] == "provider-secret-value"
    assert fake.requested == ["database", "signing", "provider"]


def test_database_secret_rejects_unknown_fields() -> None:
    """Verify runtime database secret schema is exact rather than permissive."""
    with pytest.raises(RuntimeSecretError, match="database_secret_schema_invalid"):
        build_database_conninfo(
            {
                "username": "sag_runtime",
                "password": "x" * 48,
                "host": "should-not-be-secret",
            },
            host="db.internal.example",
            port="5432",
            database="secure_ai_gateway",
        )


def test_load_json_secret_rejects_non_object() -> None:
    """Verify runtime secret loader fails closed on unexpected JSON structure."""
    fake = _FakeSecretsManager({"bad": ["not", "an", "object"]})
    with pytest.raises(RuntimeSecretError, match="secret_json_object_required"):
        load_json_secret("bad", client=fake)
