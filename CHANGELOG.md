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
- Clean-run zero-cost end-to-end portfolio demo in CI.
- Runtime resilience smoke test covering Redis/PostgreSQL fail-closed behavior and telemetry independence.
- Two-replica kind/Kubernetes verification.
- Terraform AWS reference architecture with explicit billable-resource guard.
- Public GHCR release workflow with anonymous-pull verification.
- Bandit, CodeQL, and `pip-audit` security analysis.
- CycloneDX Python dependency SBOM generation in CI.
- Trivy container vulnerability scanning.
- SHA-pinned GitHub Actions.
- Dependabot maintenance for Python, GitHub Actions, and Docker dependencies.
- Read-only repository preflight covering release-critical files, tracked secret/artifact hygiene, immutable Action refs, and optional full-history sensitive-filename scanning.

### Documentation and developer experience

- Reviewer-focused README with architecture and execution-authorization diagrams.
- Consolidated reviewer architecture/trust-boundary guide.
- Portfolio evidence guide mapping security claims to reproducible commands and CI evidence.
- Threat/security review and residual-risk record.
- Cost policy separating the free verified path from optional paid AWS architecture.
- Reliability/failure-injection documentation.
- Architecture decision records for execution-time tool authorization and the zero-cost required path.
- `CONTRIBUTING.md`, release checklist, `.editorconfig`, and developer Make targets.
- `make preflight` release/repository hygiene command.

## Planned v1.0.0

`v1.0.0` is reserved for the first portfolio release after the release checklist is completed. The remaining owner decision is primarily software licensing (or an explicit choice to remain unlicensed), followed by the deliberate release tag.
