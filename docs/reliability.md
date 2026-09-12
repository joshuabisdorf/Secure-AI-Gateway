# Reliability and failure injection

The gateway distinguishes security-critical shared state from observability. The zero-cost resilience smoke test makes that distinction executable.

Run locally with Docker:

```bash
make resilience
```

The script uses the deterministic mock provider and the tracked portfolio-demo policy. It starts PostgreSQL, Redis, the OpenTelemetry Collector, and the gateway, creates a temporary PostgreSQL-backed client, and then injects controlled service outages without deleting volumes.

## Verified failure contract

The CI `Resilience smoke` gate verifies:

| Injection | Expected gateway behavior |
| --- | --- |
| Redis unavailable | authenticated chat returns HTTP 503 rather than bypassing the distributed rate limiter |
| Redis restored | authenticated chat recovers to HTTP 200 |
| PostgreSQL unavailable | authentication returns HTTP 503 rather than accepting unverifiable identity |
| PostgreSQL restored | authenticated chat recovers to HTTP 200 |
| OpenTelemetry Collector unavailable | authenticated chat remains HTTP 200; tracing is not an authorization dependency |

The distinction is intentional:

- identity, usage, rate-limit, policy, signing, and replay state participate in security decisions and fail closed for the affected request;
- telemetry export is diagnostic and must not grant or revoke application authority.

A successful run ends with:

```text
secure_ai_gateway_resilience=PASS
redis_fail_closed=true
postgres_fail_closed=true
telemetry_fail_open_for_availability=true
provider_calls=mock_only
billable_cloud_resources=0
```

The temporary key is revoked on exit. The script never calls `docker compose down -v` and does not delete persistent Docker volumes.

## Scope

This test proves bounded recovery/failure behavior for the local Compose topology. It does not claim to simulate every distributed failure mode, network partition, database failover, Redis cluster event, kernel failure, or cloud managed-service behavior. The optional AWS reference architecture remains runtime-unverified unless deliberately deployed.
