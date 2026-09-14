# Changelog

All notable project changes are summarized here. The project has not yet published a stable semantic-versioned release.

## Unreleased

### Security architecture

- PostgreSQL-backed gateway client/key identity with revocation and atomic rotation.
- Newly generated raw API keys are delivered only through explicit exclusive 0600 secret files rather than normal command output; failed provisioning removes partial files, and rotation writes the replacement secret before revoking prior keys inside the database transaction.
- Distributed Redis/Valkey rate limiting and one-time execution-ticket replay protection.
- Daily persistent usage budgets.
- PostgreSQL usage-ledger pool acquisition is bounded so connection exhaustion fails closed instead of waiting indefinitely.
- Structured and semantic PII controls.
- Prompt-injection detection with regression benchmark.
- Least-privilege tool exposure plus execution-time authorization using short-lived signed tickets.
- Sensitive-data-minimized audit logging, Prometheus metrics, and OpenTelemetry tracing.
- Request-body, response-header, API-documentation, and Uvicorn runtime hardening.
- Strict canonical execution-ticket encoding.

### Verification and delivery

- Docker Compose local stack.
- Clean-run zero-cost end-to-end demo in CI.
- Runtime resilience smoke test covering Redis/PostgreSQL fail-closed behavior and telemetry independence.
- M10 deterministic adversarial corpus covering malformed HTTP/JSON, API-key syntax fuzzing, execution-ticket mutation, JSON Schema edge cases, malformed/timeout provider responses, audit leakage, and stale-policy invalidation.
- M10 real Redis/PostgreSQL concurrency suite covering execution-ticket replay races, rate-limit contention, atomic usage updates, key rotation/revocation races, bounded connection-pool exhaustion, and migration rollback.
- M10 two-replica local runtime verifier with mock-provider RPS/latency/RSS measurements, PII/injection matched-path comparison, distributed limiter throughput, and replica-termination failover verification.
- Two-replica kind/Kubernetes verification.
- Terraform AWS reference architecture with explicit billable-resource guard.
- Public GHCR release workflow with anonymous-pull verification.
- Bandit, CodeQL, and `pip-audit` security analysis.
- CycloneDX Python dependency SBOM generation in CI.
- Trivy container vulnerability scanning.
- SHA-pinned GitHub Actions.
- Dependabot maintenance for Python, GitHub Actions, and Docker dependencies.
- Read-only repository preflight covering release-critical files, tracked secret/artifact hygiene, immutable Action refs, and optional full-history sensitive-filename scanning.

### Documentation, licensing, and developer experience

- Project README with architecture, request flow, security controls, verification status, local development, and release information written in project-author voice.
- Consolidated architecture and trust-boundary guide.
- Reproducible evidence guide mapping security claims to commands and CI evidence.
- Threat/security review and residual-risk record.
- Cost policy separating the free verified path from optional paid AWS architecture.
- Reliability/failure-injection documentation with explicit fail-open/fail-closed contracts.
- Local performance-baseline methodology and reproducible benchmark command.
- Architecture decision records for execution-time tool authorization and the zero-cost default path.
- `CONTRIBUTING.md`, release checklist, `.editorconfig`, and developer Make targets.
- `make preflight` release/repository hygiene command.
- Apache License 2.0 licensing with a project `NOTICE` preserving Joshua Bisdorf attribution.
- Contribution guidance aligned with Apache-2.0 Section 5.

## Planned v1.0.0

`v1.0.0` is reserved for the first stable release after the release checklist is completed. Apache-2.0 has been selected for the project. Remaining work is focused on supply-chain and Kubernetes hardening, final security review, and the deliberate release tag.
