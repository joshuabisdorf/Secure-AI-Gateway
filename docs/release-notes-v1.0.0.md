# Secure AI Gateway v1.0.0

`v1.0.0` is the first stable portfolio release of Secure AI Gateway.

The release establishes the gateway as a security-focused control plane between
LLM applications and providers while keeping the complete required verification
path free of paid cloud infrastructure and paid model-provider calls.

## Security boundary

The gateway treats model input and output as untrusted. Authentication resolves
a gateway-issued client/key identity; model, rate, usage, PII,
prompt-injection, and tool policy remain separate authorization decisions.

Model-generated tool calls do not authorize side effects. A validated proposal
receives a short-lived signed execution ticket. A downstream executor must call
the gateway again, re-authenticate, pass current policy and ticket validation,
and atomically consume the one-time execution ID.

PostgreSQL is authoritative for client/key and persistent usage state.
Redis/Valkey is authoritative for distributed rate and replay state. Required
security-state failures are fail-closed.

## Release highlights

- PostgreSQL-backed client key creation, revocation, and atomic rotation.
- Distributed rate limiting and one-time execution-ticket replay protection.
- Persistent token/cost usage budgets.
- Structured and semantic PII controls.
- Prompt-injection detection with published regression results.
- Execution-time tool authorization with authoritative JSON Schema validation.
- Sensitive-data-minimized audit, metric, and trace surfaces.
- Hardened Docker and two-replica Kubernetes runtime configuration.
- Enforced `restricted` Pod Security and default-deny NetworkPolicies.
- Exact release dependency lock and direct-artifact SHA-256 pinning.
- CodeQL, Bandit, `pip-audit`, Trivy, dependency SBOM, and repository preflight.
- Immutable public GHCR images with SPDX SBOM and provenance attestations.
- Project-owned whole-repository style enforcement with an 80-character line
  limit and RMEIO function contracts.

## Verification

The release candidate is required to pass:

```text
make style
make check
make demo
make resilience
make lock-verify
make preflight
make terraform-validate
make kind-up && make kind-verify
```

GitHub CI independently runs Pytest, security evaluation baselines, release-lock
installation, M10 adversarial/concurrency/runtime verification, Docker build,
Kubernetes validation, live kind verification, Terraform validation, CodeQL,
container scanning, project style, and full-history repository preflight.

The public release workflow must publish the immutable SHA image, resolve its
OCI digest, produce build-provenance and SPDX SBOM attestations, and prove an
anonymous pull. The tagged run must publish `:v1.0.0`, prove it resolves to the
same digest, and anonymously pull that tag too.

## Known limitations

- Prompt-injection detection is heuristic and has published false positives and
  false negatives on the curated regression corpus.
- Semantic PII evaluation is a small regression corpus rather than a broad
  production-accuracy estimate.
- The optional AWS architecture is statically validated but is not required to
  be live-deployed.
- The repository does not implement the external side-effecting tool executor;
  it implements the authorization boundary for one.
- Internet ingress, TLS termination, WAF/DDoS controls, DNS, and certificate
  lifecycle remain deployment responsibilities.

The complete accepted-risk record is in `docs/security-review.md`.

## Compatibility

The stable API surface is the documented OpenAI-compatible chat endpoint plus
the gateway's explicit execution-authorization extension. Stable-release
changes are expected to preserve documented request/configuration contracts
within the `1.x` line unless a security correction requires otherwise.

Database migrations are forward-oriented. Operators with persistent data should
review the changelog and back up authoritative PostgreSQL data before upgrades.

## License and attribution

Secure AI Gateway remains licensed under Apache License 2.0. Project attribution
is retained in `NOTICE`. The project-owned style standard does not incorporate
or vendor an external style guide or linter policy.
