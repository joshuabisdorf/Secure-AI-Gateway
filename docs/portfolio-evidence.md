# Portfolio evidence

This page maps project claims to reproducible commands and CI evidence so a
reviewer does not have to reconstruct the development history.

## Core evidence

| Evidence | What it demonstrates | Reproduction |
| --- | --- | --- |
| `make demo` | authenticated request path, usage, PII redaction, injection detection, ticket issuance, execution authorization, replay denial, rate limiting, metrics | local Docker Compose, mock provider |
| `make resilience` | Redis/PostgreSQL fail closed; telemetry outage remains non-authoritative | local Docker Compose, mock provider |
| `make m10-adversarial` | malformed input, key/ticket mutation, schema edge cases, provider failure, audit leakage, stale policy | local Python |
| `make m10-integration` | real Redis/PostgreSQL replay, rate, usage, key lifecycle, pool exhaustion, migration rollback | local PostgreSQL + Redis |
| `make benchmark` | RPS/latency/RSS, inspection overhead, limiter contention, two replicas, termination failover | local PostgreSQL + Redis |
| `make lock-verify` | exact release pins, artifact hash, root dependency coverage, build-backend pin | repository only |
| `make style` | whole-repository project-owned style enforcement | repository only |
| `make check` | style, tests, evaluation baselines, Bandit, dependency audit | local Python |
| `make kind-up && make kind-verify` | Pod Security, default deny, shared state, telemetry, rescheduling, bounded load/resources | local kind |
| `make terraform-validate` | formatting/static validity of both Terraform roots | local Terraform |
| `make preflight` | release files, tracked artifacts, Action pins, secret markers | Git checkout |

## Deterministic demo evidence

Expected success signals include:

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

Persistent development volumes can change numeric usage counters. The security
PASS/FAIL assertions are deterministic.

## CI evidence

The primary CI workflow separates failure domains into independent jobs for:

- Pytest;
- reproducible clean installation from the exact release lock;
- M10 adversarial/reliability/performance verification;
- security analysis and dependency SBOM generation;
- prompt-injection and semantic-PII benchmarks;
- Docker build/configuration validation;
- the end-to-end demo;
- resilience smoke;
- Kubernetes manifest/schema validation;
- live kind security/resilience verification;
- Terraform validation.

Separate workflows provide project style enforcement, CodeQL, Trivy container
scanning, full-history repository preflight, and public GHCR publication.

## M10 evidence

Real Redis/PostgreSQL tests assert one-time replay, distributed rate caps,
lossless concurrent usage updates, key lifecycle invariants, bounded pool
exhaustion, and transaction rollback.

The runtime verifier reports single/two-replica RPS, p50/p95/p99 latency,
resident memory, matched PII/injection-path latency, Redis limiter contention,
and replica-termination failover. Performance numbers are evidence from the
specific local/CI environment rather than production capacity guarantees.

See `docs/performance.md` for methodology and representative results.

## M11 evidence

`requirements/release.lock` contains the exact release-runtime dependency set.
CI installs it into a clean Python environment, installs the application with
dependency resolution/build isolation disabled, runs `pip check`, and imports
the production server/model. The Docker build consumes the same lock.

The release workflow resolves the pushed image digest, generates an SPDX image
SBOM, creates GitHub build-provenance and SBOM attestations, and proves public
anonymous pull.

The live kind job enforces the `restricted` Pod Security profile and local
NetworkPolicies. It proves an otherwise untrusted pod cannot connect to Redis,
verifies allowed gateway state sharing, deletes a gateway replica, waits for a
distinct replacement, and runs a bounded request burst while checking resource
limits/restarts/OOM state.

The cloud Kubernetes overlay contains no placeholder backend CIDRs. Its backend
policies are rendered from Terraform outputs. Terraform enables EKS VPC CNI
NetworkPolicy and creates a private Secrets Manager endpoint.

## M12 final-review evidence

`docs/security-review.md` walks each trust boundary and records:

- runtime/build/deployment secret classes and storage paths;
- attacker-controlled and untrusted fields;
- authentication versus authorization separation;
- fail-open/fail-closed behavior;
- audit/metric/trace leakage controls;
- replay, concurrency, and resource-exhaustion controls;
- Kubernetes and supply-chain boundaries;
- accepted residual risks for `v1.0.0`.

`docs/architecture.md` provides the corresponding system map. The release
checklist requires the final release commit to pass all CI/security gates before
an annotated stable tag is created.

## Style evidence

The project uses a self-contained repository-owned style standard. No external
style guide or linter policy is vendored or adopted as the governing standard.

The hard rule is an 80-character line limit except for genuinely indivisible
content. Python project functions use ordered RMEIO contracts. The checker also
covers text hygiene, Python syntax/naming and structural hazards, and Bash
entrypoint/strict-mode requirements.

`make style` checks the entire tracked project tree. CI runs the same command.
There is no legacy baseline, moving exemption, or grandfathered file set.

## Security benchmark evidence

The versioned corpora are regression tests rather than claims of universal
classifier quality. Published false-positive/false-negative counts stay visible
in the README and benchmark outputs.

## Release evidence

For `v1.0.0`, retain these linked identities:

```text
release commit -> annotated v1.0.0 tag
       |
       +-> sha-<commit> image -> OCI digest
                                  |
                                  +-> build provenance
                                  +-> SPDX SBOM attestation
                                  +-> v1.0.0 image alias
```

The final release evidence must include:

- release commit SHA and annotated tag;
- immutable SHA-tagged GHCR image and OCI digest;
- `:v1.0.0` resolving to that same digest;
- anonymous pull verification for both tags;
- successful build-provenance and SPDX SBOM verification;
- green CI, CodeQL, Trivy, style, preflight, kind, and Terraform gates;
- `CHANGELOG.md`, release notes, and accepted residual risks.

The release workflow provides machine-verifiable image evidence. The GitHub
release page may record the final commit/tag/digest values after publication.

## Evidence quality rule

Prefer reproducible commands and CI logs over screenshots. Screenshots can help
reviewers scan the project quickly, but each screenshot should point to a
command, workflow, test, benchmark, or documented invariant that can be
reproduced.

Never capture raw API keys, execution tickets, provider credentials, `.env`
content, Terraform state, or raw sensitive request payloads in portfolio
evidence.
