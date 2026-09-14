# Local performance baseline

This project maintains a reproducible local performance baseline for Secure AI Gateway. The baseline uses the deterministic mock provider and local PostgreSQL/Redis only. It is intended to detect large regressions and document the cost of the gateway control plane; it is **not** a production capacity claim or cloud sizing recommendation.

## Run the baseline

Start the shared state services and expose their local URLs:

```bash
docker compose up -d postgres redis
export DATABASE_URL='postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway'
export REDIS_URL='redis://127.0.0.1:6379/15'
```

Run the real-backend concurrency suite and the runtime baseline:

```bash
make m10-integration
make benchmark
```

The runtime verifier can also be invoked directly:

```bash
python scripts/m10_runtime_verification.py \
  --requests 160 \
  --concurrency 16 \
  --rate-limit-operations 400 \
  --output /tmp/m10-runtime.json
```

CI uses a smaller bounded sample (`80` requests at concurrency `8`) to keep verification deterministic and inexpensive.

## What is measured

The verifier creates one temporary PostgreSQL-backed gateway client and starts two single-worker Uvicorn processes on loopback. Both replicas share the same PostgreSQL database and Redis instance. Requests use the same authentication, model authorization, PII inspection, prompt-injection inspection, rate limiting, usage accounting, auditing, and mock-provider path used by the application.

The JSON report records:

- single-replica requests/sec and p50/p95/p99 end-to-end latency;
- two-replica requests/sec and p50/p95/p99 latency;
- Linux resident-set size (RSS) for each replica when `/proc` is available;
- p50 latency for a normal request and for a request that exercises structured PII plus prompt-injection matches, reported as a detection-path delta rather than isolated detector cost;
- direct distributed Redis rate-limiter operations/sec under concurrent access;
- concurrent traffic while one gateway process is terminated, including bounded alternate-replica retries and a forced post-termination failover check.

The temporary gateway key is revoked during cleanup. The verifier does not print the credential, call an external provider, create cloud resources, or enforce a minimum throughput threshold.

## Interpretation

The one-replica and two-replica figures are local process baselines. They include loopback HTTP, authentication, local PII/injection inspection, Redis rate limiting, PostgreSQL usage accounting, audit generation, and the mock provider. They do not include real model-provider latency, TLS over a wide-area network, cloud load-balancer behavior, managed database latency, autoscaling, or noisy-neighbor effects.

A two-replica result is not expected to scale linearly on a single CI host because both replicas compete for the same CPU, memory, PostgreSQL, and Redis resources. Its purpose is to verify that shared controls remain functional across processes and to provide a repeatable comparison point.

The inspection-path delta is similarly descriptive. Both the normal and matched requests pass through the security inspection pipeline; the matched request additionally exercises redaction and prompt-injection indicator handling. The difference must not be interpreted as the total cost of enabling security controls.

## Representative result

A representative GitHub Actions result is recorded after the M10 pull request completes on the hosted runner. Host-level measurements vary across runs, so the repository treats the command and methodology as authoritative and the numeric result as a dated example rather than a release threshold.
