import asyncio

import pytest
from botocore.credentials import Credentials

from app.redis_client import (
    ElastiCacheIamCredentialProvider,
    RedisClientConfigurationError,
    build_redis_client,
)


class _FakeSession:
    def get_credentials(self) -> Credentials:
        return Credentials("AKIDEXAMPLE", "secret-example", "session-example")


def test_elasticache_iam_credentials_are_short_lived_and_cached() -> None:
    """
    RME

    Requires:
        - ElastiCache IAM tokens can be signed with injected AWS credentials.

    Modifies:
        - Process-local credential-provider token cache and test clock.

    Effects:
        - Verifies generated credentials use user ID plus a SigV4 connect token.
        - Verifies repeated requests before refresh reuse the token.
        - Verifies the async credentials interface returns the same credential shape.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine provider behavior.
    """
    now = [100.0]
    provider = ElastiCacheIamCredentialProvider(
        user_id="sag-dev-gateway",
        cache_name="sag-dev-cache",
        region="ca-central-1",
        session=_FakeSession(),
        clock=lambda: now[0],
    )

    first = provider.get_credentials()
    second = provider.get_credentials()
    async_value = asyncio.run(provider.get_credentials_async())

    assert first == second == async_value
    assert first[0] == "sag-dev-gateway"
    assert first[1].startswith("sag-dev-cache/?")
    assert "Action=connect" in first[1]
    assert "User=sag-dev-gateway" in first[1]
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in first[1]
    assert "secret-example" not in first[1]


def test_elasticache_iam_requires_tls(monkeypatch) -> None:
    """Verify IAM authentication cannot be configured over plaintext Redis transport."""
    monkeypatch.setenv("SAG_REDIS_AUTH_MODE", "elasticache_iam")
    monkeypatch.setenv("SAG_ELASTICACHE_USER_ID", "sag-dev-gateway")
    monkeypatch.setenv("SAG_ELASTICACHE_CACHE_NAME", "sag-dev-cache")
    monkeypatch.setenv("AWS_REGION", "ca-central-1")

    with pytest.raises(RedisClientConfigurationError, match="requires_tls"):
        build_redis_client("redis://cache.example:6379/0")


def test_elasticache_iam_rejects_embedded_credentials(monkeypatch) -> None:
    """Verify IAM-mode connection URLs cannot also carry static username/password credentials."""
    monkeypatch.setenv("SAG_REDIS_AUTH_MODE", "elasticache_iam")
    monkeypatch.setenv("SAG_ELASTICACHE_USER_ID", "sag-dev-gateway")
    monkeypatch.setenv("SAG_ELASTICACHE_CACHE_NAME", "sag-dev-cache")
    monkeypatch.setenv("AWS_REGION", "ca-central-1")

    with pytest.raises(RedisClientConfigurationError, match="must_not_embed_credentials"):
        build_redis_client("rediss://user:password@cache.example:6379/0")
