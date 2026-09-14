# Reliability and failure injection

Secure AI Gateway treats security-critical shared state as an authorization dependency and observability as a diagnostic dependency. Required security state fails closed; telemetry export is deliberately non-authoritative.

## Verification commands

The existing Docker resilience smoke test remains the fast dependency-outage path:

```bash
make resilience
```

M10 adds deterministic adversarial coverage plus real Redis/PostgreSQL concurrency verification:

```bash
make m10-adversarial

export DATABASE_URL='postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway'
export REDIS_URL='redis://127.0.0.1:6379/15'
docker compose up -d postgres redis
make m10-integration
make benchmark
```

The CI job `M10 adversarial/reliability/performance` provisions isolated PostgreSQL and Redis service containers and runs the real-backend integration tests plus the two-replica mock-provider verifier. No paid provider or cloud resource is used.

## Failure contract

| Dependency or failure | Contract | Verification |
| --- | --- | --- |
| Redis unavailable during chat rate limiting | **Fail closed.** The request returns HTTP 503 rather than bypassing the distributed limit. | `make resilience` |
| Redis restored | Requests recover without changing policy or credentials. | `make resilience` |
| PostgreSQL unavailable during authentication | **Fail closed.** Identity cannot be verified and the request returns HTTP 503. | `make resilience` |
| PostgreSQL restored | Requests recover using the same persisted client identity. | `make resilience` |
| Redis execution-ticket replay store unavailable | **Fail closed.** Execution authorization returns HTTP 503; one-time semantics are never bypassed. | tool-execution tests plus the real Redis claim-race test |
| Concurrent replay attempts | Exactly one distributed `SET NX` claim succeeds for a valid execution ID. | `tests/test_m10_integration.py` |
| Concurrent distributed rate-limit checks | At most the configured window capacity is granted across independent limiter instances. | `tests/test_m10_integration.py` |
| Concurrent PostgreSQL usage updates | Atomic upserts preserve every token/cost increment. | `tests/test_m10_integration.py` |
| Concurrent key rotations | Client-row locking serializes rotations and leaves exactly one active key. | `tests/test_m10_integration.py` |
| Explicit revocation racing rotation | **Fail closed.** The race may intentionally leave zero active keys, but never creates multiple active credentials. | `tests/test_m10_integration.py` |
| PostgreSQL usage-ledger pool exhaustion | **Fail closed and bounded.** Pool acquisition has a finite timeout and becomes `UsageLedgerUnavailable`, which the HTTP path maps to 503. | `tests/test_m10_integration.py` |
| Provider timeout | **Fail closed for the request.** Timeout becomes `ProviderError("upstream_timeout")`; the gateway maps provider failures to HTTP 502. | `tests/test_m10_adversarial.py` |
| Provider returns malformed success payload | **Fail closed for the request.** Invalid JSON/schema becomes `ProviderError("invalid_upstream_response")` and HTTP 502 at the gateway boundary. | `tests/test_m10_adversarial.py` |
| Database migration statement fails | **Fail closed.** Statements and migration metadata from that migration roll back together; migration execution fails. | `tests/test_m10_integration.py` |
| Security policy file changes | New valid policy is reloaded; an invalid replacement does not fall back to stale cached grants. | `tests/test_m10_adversarial.py` |
| Execution policy changes after ticket issuance | A stale ticket is rejected when its schema/risk binding no longer matches current policy. | `tests/test_m10_adversarial.py` |
| One gateway replica terminates during local concurrent load | Requests with one bounded alternate-replica retry continue through the surviving process; a forced post-termination retry verifies the dead replica is not required. | `scripts/m10_runtime_verification.py` |
| OpenTelemetry Collector unavailable | **Fail open for application availability.** Authenticated chat remains available because telemetry has no authorization authority. | `make resilience` |

Security policy, identity, usage accounting, rate limiting, signing, and replay state participate in security decisions and therefore fail closed for the affected operation. Telemetry export does not grant or revoke authority and is allowed to fail independently.

## Adversarial verification

`tests/test_m10_adversarial.py` extends the normal unit suite with deterministic corpora for malformed JSON/body framing, invalid and fuzzed API-key syntax, execution-ticket encoding and semantic mutations, JSON Schema argument boundaries, malformed provider responses, provider timeouts, audit-log secret leakage, security-policy cache invalidation, and execution-policy changes.

The corpus is deterministic and network-free. It does not use random external input or a paid fuzzing service; the pseudo-random API-key corpus uses a fixed seed so failures are reproducible.

## Existing resilience smoke

The Compose resilience path starts PostgreSQL, Redis, the OpenTelemetry Collector, and the gateway with the deterministic mock provider. It creates a temporary PostgreSQL-backed client and injects controlled service outages without deleting volumes.

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

These tests establish deterministic behavior for the local process/Compose topology and real local Redis/PostgreSQL concurrency. The two-replica termination test verifies process redundancy and bounded client-side alternate-replica retry; it is not a claim about Kubernetes Service routing, cloud load balancers, database failover, Redis cluster partitions, kernel failure, or production capacity. Kubernetes-specific topology hardening and load behavior remain separate deployment concerns.
