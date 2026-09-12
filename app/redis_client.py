import asyncio
import os
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from redis.asyncio import Redis
from redis.credentials import CredentialProvider
from redis.exceptions import AuthenticationError

_ELASTICACHE_TOKEN_SECONDS = 900
_ELASTICACHE_REFRESH_SECONDS = 720


class RedisClientConfigurationError(ValueError):
    """Raised when shared Redis/Valkey client configuration is unsafe or incomplete."""


class ElastiCacheIamCredentialProvider(CredentialProvider):
    def __init__(
        self,
        *,
        user_id: str,
        cache_name: str,
        region: str,
        session: boto3.Session | None = None,
        clock=time.monotonic,
    ) -> None:
        """
        RME

        Requires:
            - user_id identifies an IAM-authenticated ElastiCache user whose name equals its ID.
            - cache_name identifies a node-based ElastiCache replication group.
            - region identifies the AWS region containing the cache.

        Modifies:
            - Process-local short-lived IAM token cache.

        Effects:
            - Uses the AWS default credential chain to create SigV4 ElastiCache connect tokens.
            - Caches each token for less than its 15-minute maximum lifetime.
            - Never persists or logs AWS credentials or generated cache tokens.

        Inputs:
            - user_id: ElastiCache IAM user ID/name.
            - cache_name: ElastiCache replication-group name.
            - region: AWS region.
            - session: Optional injectable boto3 session for deterministic tests.
            - clock: Monotonic time source used for token-cache expiry.

        Outputs:
            - redis-py credential provider returning username/token pairs.
        """
        for label, value in (
            ("user_id", user_id),
            ("cache_name", cache_name),
            ("region", region),
        ):
            if not value or not value.strip():
                raise RedisClientConfigurationError(f"missing_elasticache_{label}")

        self._user_id = user_id.strip()
        self._cache_name = cache_name.strip().lower()
        self._region = region.strip()
        self._session = session or boto3.Session()
        self._clock = clock
        self._lock = threading.Lock()
        self._cached_token: str | None = None
        self._refresh_at = 0.0

    def _generate_token(self) -> str:
        credentials = self._session.get_credentials()
        if credentials is None:
            raise AuthenticationError("AWS credentials are unavailable for ElastiCache IAM auth")

        frozen = credentials.get_frozen_credentials()
        request = AWSRequest(
            method="GET",
            url=(
                f"http://{self._cache_name}/?"
                + urlencode({"Action": "connect", "User": self._user_id})
            ),
        )
        SigV4QueryAuth(
            frozen,
            "elasticache",
            self._region,
            expires=_ELASTICACHE_TOKEN_SECONDS,
        ).add_auth(request)
        if not request.url.startswith("http://"):
            raise AuthenticationError("invalid ElastiCache IAM token response")
        return request.url[len("http://") :]

    def get_credentials(self) -> tuple[str, str]:
        """Return a cached or newly generated IAM username/token pair."""
        now = self._clock()
        if self._cached_token is not None and now < self._refresh_at:
            return (self._user_id, self._cached_token)

        with self._lock:
            now = self._clock()
            if self._cached_token is None or now >= self._refresh_at:
                self._cached_token = self._generate_token()
                self._refresh_at = now + _ELASTICACHE_REFRESH_SECONDS
            return (self._user_id, self._cached_token)

    async def get_credentials_async(self) -> tuple[str, str]:
        """Generate credentials without blocking the async Redis connection path."""
        return await asyncio.to_thread(self.get_credentials)


def _elasticache_iam_provider() -> ElastiCacheIamCredentialProvider:
    user_id = os.getenv("SAG_ELASTICACHE_USER_ID", "")
    cache_name = os.getenv("SAG_ELASTICACHE_CACHE_NAME", "")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or ""
    return ElastiCacheIamCredentialProvider(
        user_id=user_id,
        cache_name=cache_name,
        region=region,
    )


def build_redis_client(redis_url: str, **kwargs: Any) -> Redis:
    """
    RME

    Requires:
        - redis_url identifies the configured shared Redis/Valkey endpoint.
        - SAG_REDIS_AUTH_MODE is either none or elasticache_iam.
        - elasticache_iam mode uses rediss:// and complete AWS/cache identity configuration.

    Modifies:
        - Initializes process-local Redis connection-pool state.

    Effects:
        - Centralizes local Redis and AWS ElastiCache IAM/TLS client construction.
        - Rejects password/username URLs when IAM authentication is selected.

    Inputs:
        - redis_url: Redis-compatible URL.
        - kwargs: Additional redis-py connection options.

    Outputs:
        - Configured asynchronous redis-py client.
    """
    if not redis_url or not redis_url.strip():
        raise RedisClientConfigurationError("redis_url_not_configured")

    auth_mode = os.getenv("SAG_REDIS_AUTH_MODE", "none").strip().lower()
    if auth_mode == "none":
        return Redis.from_url(redis_url, **kwargs)

    if auth_mode != "elasticache_iam":
        raise RedisClientConfigurationError("unsupported_redis_auth_mode")

    parsed = urlsplit(redis_url)
    if parsed.scheme.lower() != "rediss":
        raise RedisClientConfigurationError("elasticache_iam_requires_tls")
    if parsed.username is not None or parsed.password is not None:
        raise RedisClientConfigurationError("elasticache_iam_url_must_not_embed_credentials")

    provider = _elasticache_iam_provider()
    return Redis.from_url(
        redis_url,
        credential_provider=provider,
        **kwargs,
    )
