# Changelog

All notable project changes are summarized here.

## Unreleased

No unreleased changes are currently documented.

## 1.0.0 - 2026-09-14

First stable portfolio release.

### Security architecture

- PostgreSQL-backed client/key identity with revocation and atomic rotation.
- Raw API keys are delivered only through explicit exclusive `0600` files;
  PostgreSQL stores one-way key digests rather than raw keys.
- Distributed Redis/Valkey rate limiting and one-time execution-ticket replay
  protection.
- Persistent daily token/cost usage budgets with bounded database pool waits.
- Structured and semantic PII controls with redact/deny policy.
- Prompt-injection audit/deny controls with a versioned regression benchmark.
- Least-privilege tool exposure plus independent execution-time authorization.
- Short-lived signed execution tickets bind identity, arguments, authoritative
  schema, risk, source request, and expiry.
- Sensitive-data-minimized audit logging, Prometheus metrics, and
  OpenTelemetry tracing.
- Request-body, response-header, API-documentation, and Uvicorn hardening.
- Strict canonical execution-ticket syntax.
- Kubernetes `restricted` Pod Security enforcement and overlay-specific
  default-deny NetworkPolicies.
- AWS cloud egress policy rendered from Terraform subnet CIDRs with private
  Secrets Manager access and EKS Pod Identity.

### Verification and delivery

- Docker Compose local stack and deterministic mock-provider demo.
- Resilience verification for Redis/PostgreSQL fail-closed behavior and
  telemetry independence.
- Adversarial coverage for malformed HTTP/JSON, API-key/ticket mutation,
  schema edge cases, provider failure, audit leakage, and stale policy.
- Real Redis/PostgreSQL concurrency tests for replay, rate limits, usage,
  key lifecycle, connection exhaustion, and migration rollback.
- Two-replica runtime measurement for RPS, latency, RSS, inspection overhead,
  distributed limiter contention, and replica failover.
- Live kind verification for Pod Security, default-deny networking, shared
  state, replica rescheduling, and bounded load/resource behavior.
- Terraform AWS reference architecture with explicit billable-resource guard.
- Exact release-runtime dependency lock and clean-environment installation.
- SHA-256 pinning for the direct spaCy model artifact.
- Public GHCR release workflow with immutable SHA images, anonymous pulls,
  SPDX image SBOM, build provenance, and SBOM attestations.
- Tagged releases verify that the semver image resolves to the same immutable
  OCI digest and is anonymously pullable.
- Bandit, CodeQL, `pip-audit`, CycloneDX dependency SBOM, and Trivy scanning.
- SHA-pinned GitHub Actions and Dependabot maintenance.
- Repository/history preflight for release-critical files, secrets/artifacts,
  and immutable Action references.

### Documentation and developer experience

- Reviewer-oriented README, architecture, threat/security review, and evidence
  guides.
- Explicit fail-open/fail-closed reliability contracts and local performance
  methodology.
- Supply-chain, dependency, Kubernetes security/identity, and cost-policy
  documentation.
- Architecture decisions for execution-time tool authorization and the
  zero-cost required path.
- Project-owned source/documentation style standard with an 80-character line
  limit, RMEIO contracts, and standard-library-only enforcement.
- Whole-repository style enforcement on every CI run with no legacy baseline or
  grandfathered file set.
- `CONTRIBUTING.md`, `.editorconfig`, release checklist, and Make targets.
- Apache License 2.0 with `NOTICE` preserving Joshua Bisdorf attribution.
