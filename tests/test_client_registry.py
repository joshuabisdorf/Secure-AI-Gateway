import asyncio

import pytest

from app.client_registry import (
    ClientRegistryUnavailable,
    EnvironmentClientRegistry,
    UnavailableClientRegistry,
    build_client_registry,
)


def test_environment_registry_returns_hashed_record(monkeypatch) -> None:
    """
    RME

    Requires:
        - The environment registry reads SAG_CLIENTS on each lookup.

    Modifies:
        - Temporarily configures SAG_CLIENTS.

    Effects:
        - Verifies the test/legacy backend resolves hashed client identity metadata.

    Inputs:
        - monkeypatch: pytest environment fixture.

    Outputs:
        - None. Assertions determine whether lookup works correctly.
    """
    monkeypatch.setenv(
        "SAG_CLIENTS",
        "client-a:keya:"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    registry = EnvironmentClientRegistry()

    record = asyncio.run(registry.get_key_record("keya"))

    assert record is not None
    assert record.client_id == "client-a"
    assert record.key_id == "keya"
    assert record.api_key_sha256 == "a" * 64


def test_environment_registry_fails_closed_when_missing(monkeypatch) -> None:
    """
    RME

    Requires:
        - SAG_CLIENTS may be absent from the test environment.

    Modifies:
        - Temporarily removes SAG_CLIENTS.

    Effects:
        - Verifies the environment backend refuses unauthenticated configuration.

    Inputs:
        - monkeypatch: pytest environment fixture.

    Outputs:
        - None. Assertions determine whether missing registry configuration fails closed.
    """
    monkeypatch.delenv("SAG_CLIENTS", raising=False)
    registry = EnvironmentClientRegistry()

    with pytest.raises(ClientRegistryUnavailable):
        asyncio.run(registry.get_key_record("keya"))


def test_postgres_backend_fails_closed_without_database_url(monkeypatch) -> None:
    """
    RME

    Requires:
        - Registry backend selection is environment-driven.

    Modifies:
        - Temporarily selects PostgreSQL and removes DATABASE_URL.

    Effects:
        - Verifies production registry configuration fails closed without a database.

    Inputs:
        - monkeypatch: pytest environment fixture.

    Outputs:
        - None. Assertions determine whether backend selection is safe.
    """
    monkeypatch.setenv("SAG_CLIENT_REGISTRY_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    registry = build_client_registry()

    assert isinstance(registry, UnavailableClientRegistry)
