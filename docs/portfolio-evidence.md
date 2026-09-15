# Portfolio evidence

This page defines the evidence a reviewer should be able to inspect quickly
without reconstructing the project history.

## Core evidence

| Evidence | What it demonstrates | Reproduction | | --- | --- | --- | |
`make demo` | authenticated request path, usage accounting, PII redaction,
prompt-injection detection, tool-ticket issuance, execution authorization,
replay denial, rate limiting, Prometheus metrics | local Docker Compose, mock
provider only | | `make resilience` | Redis and PostgreSQL fail closed;
telemetry outage does not become an authorization dependency | local Docker
Compose, mock provider only | | `make m10-adversarial` | malformed HTTP/JSON,
key/ticket mutation, schema-edge, provider-failure, audit-leakage, and
stale-policy regression coverage | local Python environment, no network/provider
calls | | `make m10-integration` | real Redis/PostgreSQL replay, rate, usage,
key-lifecycle, pool-exhaustion, and migration-rollback concurrency behavior |
local PostgreSQL + Redis | | `make benchmark` | local mock-provider
RPS/p50/p95/p99/RSS baseline, matched inspection path, distributed limiter
throughput, two replicas, and replica-termination failover | local PostgreSQL +
Redis, mock provider only | | `make lock-verify` | release lock exact pins,
direct-artifact hash pin, root dependency coverage, exact build-backend pin |
repository files only | | `make check` | unit/integration tests, security
evaluation baselines, Bandit, dependency audit | local Python environment | |
`make kind-up && make kind-verify` | restricted Pod Security, default-deny
NetworkPolicy enforcement, shared state, telemetry, replica rescheduling,
bounded load/resource behavior | local kind cluster | |
`make terraform-validate` | formatting and static validity of both Terraform
roots, including private cloud network-policy support | local Terraform | |
`make preflight` | release/repository hygiene, required files, immutable action
refs, credential/artifact checks | Git checkout only |

## Deterministic demo evidence

Expected successful demo signals include:

```text
PASS step=gateway_health
PASS step=create_demo_client ... raw_key_logged=false
PASS step=authenticated_chat status=200
PASS step=usage_accounting ...
PASS step=pii_redaction ...
PASS step=prompt_injection_detection ...
PASS step=tool_ticket_issue ... raw_ticket_logged=false
PASS step=execution_authorization status=200
PASS step=execution_replay_denied status=409
PASS step=rate_limit_enforcement status=429
PASS step=prometheus_metrics
secure_ai_gateway_demo=PASS
provider_calls=mock_only
billable_cloud_resources=0
```

The exact numeric usage counters may differ because persistent development
volumes are intentionally preserved. The PASS/FAIL security assertions are
deterministic.

## CI evidence

The primary CI workflow separates failure domains into independent jobs,
including Pytest, clean release-lock installation, M10
adversarial/reliability/performance verification, security analysis,
prompt-injection and semantic-PII benchmarks, Docker build/configuration
validation, the end-to-end demo, resilience smoke, Kubernetes manifest
validation, live kind security/resilience verification, and Terraform
validation.

Additional workflows provide CodeQL analysis, scheduled/PR container
vulnerability scanning, repository preflight, and public GHCR image publication.

The live kind job is the M11 Kubernetes acceptance path. It applies the
namespace with `restricted` Pod Security enforcement, deploys the default-deny
local NetworkPolicies, proves a non-allowed Redis connection is denied, verifies
normal cross-replica security state, deletes one gateway replica under traffic,
verifies a distinct replacement, and runs a bounded request burst while checking
resource limits/restarts/OOM state.

## M10 evidence

The M10 CI job deliberately separates correctness from performance claims. Real
Redis/PostgreSQL tests assert one-time replay behavior, distributed rate-limit
caps, lossless usage updates, key lifecycle invariants, bounded pool exhaustion,
and migration rollback. The runtime verifier asserts two-replica availability
behavior while reporting performance measurements without enforcing a
machine-specific throughput threshold.

The runtime JSON report includes single/two-replica requests per second,
p50/p95/p99 latency, resident memory where Linux `/proc` is available, matched
PII/injection-path latency, direct Redis limiter contention throughput, and
replica-termination failover evidence. `docs/performance.md` defines the
methodology and limitations.

## M11 supply-chain evidence

`requirements/release.lock` contains the exact release-runtime dependency set.
CI installs it into a fresh Python 3.13 virtual environment, installs the
application with dependency resolution/build isolation disabled, runs
`pip check`, and imports the production server and spaCy model. The Docker build
consumes the same lock on Python 3.14.

The release workflow resolves the pushed image digest, generates an SPDX JSON
SBOM from that digest, creates GitHub build-provenance and SBOM attestations,
pushes those attestations to GHCR, then proves the image remains anonymously
pullable. Verification commands are documented in `docs/supply-chain.md`.

The cloud Kubernetes overlay contains no placeholder backend CIDRs.
`scripts/render_cloud_network_policies.py` consumes actual private/data subnet
CIDRs from Terraform outputs at deployment time. Terraform also enables the EKS
VPC CNI NetworkPolicy feature and creates a private Secrets Manager interface
endpoint used by the restricted egress model.

## Security benchmark evidence

The versioned corpora are regression tests, not claims of universal detection
quality. Current documented baselines are intentionally published with
false-positive/false-negative counts so limitations remain visible.

For screenshots or a short demo recording, capture the README quick-start path,
one successful `make demo`, the GitHub Actions job matrix, Prometheus gateway
metrics, a safe OpenTelemetry trace/debug entry, the execution-authorization
sequence diagram, and the benchmark summary from `docs/performance.md`. Do not
capture raw API keys, execution tickets, provider credentials, `.env` content,
Terraform state, or raw sensitive request payloads.

## Release evidence

For `v1.0.0`, retain these verifiable facts in the repository/release page:

- release commit SHA and annotated tag;
- immutable `ghcr.io/joshuabisdorf/secure-ai-gateway:sha-<commit>` image and OCI
  digest;
- corresponding `:v1.0.0` image resolving to the reviewed digest;
- green release workflow including anonymous pull verification;
- verified build-provenance and SPDX SBOM attestations;
- current `CHANGELOG.md` and `docs/release-checklist.md`;
- documented residual risks in `docs/security-review.md`.

## Evidence quality rule

Prefer reproducible commands and CI logs over manually curated screenshots.
Screenshots are useful for fast review, but every screenshot should point back
to a repository command, workflow, test, benchmark, or documented invariant that
another person can reproduce.
