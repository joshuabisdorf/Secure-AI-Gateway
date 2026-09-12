# Changelog

All notable project changes are summarized here. The project has not yet published a stable semantic-versioned release.

## Unreleased

### Security architecture

- PostgreSQL-backed gateway client/key identity with revocation and atomic rotation.
- Distributed Redis/Valkey rate limiting and one-time execution-ticket replay protection.
- Daily persistent usage budgets.
- Structured and semantic PII controls.
- Prompt-injection detection with regression benchmark.
- Least-privilege tool exposure plus execution-time authorization using short-lived signed tickets.
- Sensitive-data-minimized audit logging, Prometheus metrics, and OpenTelemetry tracing.
- Request-body, response-header, API-documentation, and Uvicorn runtime hardening.
- Strict canonical execution-ticket encoding.

### Verification and delivery

- Docker Compose local stack.
- Two-replica kind/Kubernetes verification.
- Terraform AWS reference architecture with explicit billable-resource guard.
- Public GHCR release workflow with anonymous-pull verification.
- Bandit and `pip-audit` CI security gates.
- CycloneDX Python dependency SBOM generation in CI.
- SHA-pinned GitHub Actions and Dependabot maintenance policy.
- Zero-cost deterministic end-to-end portfolio demo.

### Documentation

- Threat/security review and residual-risk record.
- Cost policy separating the free verified path from optional paid AWS architecture.
- Release checklist and developer Make targets.

## Planned v1.0.0

`v1.0.0` is reserved for the first portfolio release after the release checklist is completed, including local `make check` and `make demo` verification on the intended release commit.
