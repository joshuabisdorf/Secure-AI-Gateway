# Final security review

This document records the project's final portfolio security review. It describes implemented boundaries and known residual risk; it is not a claim of formal verification or production certification.

## Trust boundaries

### Client to gateway

Attacker-controlled input includes authorization headers, request IDs, model names, messages, tool declarations, tool-choice metadata, and JSON body shape. The gateway authenticates structured `sag_*` keys against stored SHA-256 digests, uses constant-time digest comparison, bounds request bodies before application parsing, validates Pydantic schemas, and applies per-client policy after authentication.

### Gateway to provider

Provider credentials are gateway-side secrets and are never accepted from clients. Requests pass model authorization, PII handling, prompt-injection inspection, usage-budget checks, and tool-exposure authorization before provider forwarding. Provider errors are mapped to sanitized gateway responses.

### Provider output back to gateway

Provider/model output is untrusted. Tool calls are checked against the authenticated client's current allowlist and an authoritative execution registry. Argument JSON is parsed and schema-validated. The request-declared schema must fingerprint-match the authoritative schema.

### Gateway to downstream executor

A model tool call alone is not authority to execute. The gateway issues a short-lived HMAC-SHA256 ticket bound to client identity, key identity, source request, tool-call ID, tool name, exact argument bytes, authoritative schema fingerprint, risk class, and expiry. A downstream executor must call `/v1/tool-executions/authorize`; the gateway re-authenticates, re-checks current policy, verifies the ticket, and consumes its execution ID once through the replay store.

### Gateway to shared state

PostgreSQL is authoritative for client/key identity and daily usage. Redis/Valkey is authoritative for distributed rate-limit and ticket-replay state. Required backend failure is handled fail-closed for the relevant security decision.

## Secret inventory

Runtime secrets include:

- raw gateway client API keys;
- provider API keys;
- tool-execution HMAC signing key;
- PostgreSQL credentials;
- optional cloud runtime credentials/tokens.

Tracked policy files, model allowlists, tool schemas, risk classes, Terraform, Kubernetes manifests, and the system prompt are not treated as secrets.

The project intentionally excludes raw API keys, provider credentials, prompts, raw PII, raw tool arguments, tool results, and execution tickets from structured audit events and bounded telemetry labels.

## Attacker-controlled fields

The security review treats at least the following as attacker-controlled or untrusted:

- all HTTP request headers except infrastructure-generated transport metadata;
- all request JSON fields;
- request-provided tool schemas and tool choice;
- caller-provided request IDs;
- provider response bodies;
- model-generated tool names, IDs, and arguments;
- execution-authorization request bodies;
- malformed/oversized ticket encodings;
- dependency/backend availability and partial failure.

No model-produced string is considered an authorization decision.

## Fail-closed matrix

| Dependency / control | Failure behavior |
| --- | --- |
| API-key registry | Authentication unavailable/denied |
| Client security policy | Request denied/unavailable |
| Redis rate limiter | Chat request returns 503 |
| PostgreSQL usage ledger | Chat request returns 503 |
| Tool execution policy | Tool ticket issuance/authorization unavailable |
| Tool replay store | Execution authorization returns 503 |
| Missing/invalid signing key | Tool ticket issuance/authorization unavailable |
| Provider request | Sanitized 502; no fabricated success |
| Missing required usage data | 502 before success is returned |
| Oversized HTTP body | 413 before FastAPI request parsing |
| Malformed execution ticket | Generic authorization denial |

Observability export is intentionally not an authorization boundary. Telemetry failure must not grant additional access.

The `Resilience smoke` CI gate verifies this boundary against the actual Compose services: Redis outage returns 503 and recovers, PostgreSQL outage returns 503 and recovers, and OpenTelemetry Collector outage leaves authenticated mock-provider chat available. See [`reliability.md`](reliability.md).

## Resource-exhaustion controls

Implemented controls include bounded request bodies, Uvicorn concurrency limits, short keep-alive timeouts, per-client fixed-window request limits, daily usage budgets, bounded execution-ticket lifetime, bounded policy file size, bounded metric labels, container memory/CPU resource declarations in Kubernetes, and non-root/read-only container execution.

These controls reduce risk but do not constitute comprehensive denial-of-service protection for an Internet-facing service.

## Supply-chain controls

The repository uses:

- immutable commit SHAs for third-party GitHub Actions;
- Dependabot for Python, GitHub Actions, and Docker updates;
- Bandit static analysis;
- GitHub CodeQL Python analysis with the `security-extended` query suite;
- `pip-audit` dependency vulnerability gating;
- CycloneDX Python dependency SBOM generation in CI;
- deterministic Docker builds from a tracked Dockerfile;
- immutable `sha-*` GHCR release tags;
- anonymous-pull verification for the public release image.

GitHub secret scanning is available automatically for public repositories; repository-admin settings such as push protection remain outside application code and should be reviewed separately.

A future production deployment should additionally sign/attest release artifacts and scan the final OS/container filesystem with a dedicated container scanner.

## Kubernetes boundary

The gateway pod runs non-root with a read-only root filesystem, `RuntimeDefault` seccomp, no privilege escalation, dropped Linux capabilities, explicit resource requests/limits, disabled service-account-token automount in the local/base deployment, health/readiness probes, rolling updates, and a PodDisruptionBudget. The namespace emits restricted Pod Security Admission warnings/audit findings.

NetworkPolicy is not enforced in the shared base because local and optional cloud backends have different network identities. A production operator should add environment-specific ingress/egress NetworkPolicies once concrete DNS/CIDR/service identities are known.

## Residual risk accepted for this portfolio release

- Prompt-injection detection is heuristic and the curated benchmark has known false positives and false negatives.
- Semantic PII evaluation is a small curated regression corpus, not a population estimate.
- Real LLM providers can change behavior independently of the gateway.
- The optional AWS reference architecture is statically validated but intentionally may remain unapplied to preserve the zero-cost constraint.
- No external side-effecting tool executor is implemented; execution authorization is the enforced boundary provided by this repository.
- Internet-facing ingress, TLS termination, DDoS protection, WAF, DNS, certificate rotation, and cloud-specific NetworkPolicy are deployment responsibilities.
- GitHub repository branch protection/rulesets and optional secret-scanning/push-protection settings are account/repository administration controls and are not enforced by application code.
- Dependency audits and CodeQL identify known/pattern-detectable issues; they cannot detect every unknown vulnerability or malicious-but-unflagged dependency.
- Failure injection covers the local Compose topology and does not simulate every network partition, kernel failure, managed-service failover, or Byzantine condition.

## Architecture decisions

Two security/delivery decisions are recorded explicitly:

- [`adr/0001-execution-time-tool-authorization.md`](adr/0001-execution-time-tool-authorization.md)
- [`adr/0002-zero-cost-required-path.md`](adr/0002-zero-cost-required-path.md)

## Release decision

A `v1.0.0` tag should be created only after:

1. CI, CodeQL, and the public release workflow are green on the intended release commit;
2. the public GHCR SHA image is anonymously pullable;
3. the clean-run CI demo and resilience smoke pass;
4. `make check` passes locally or equivalently on the final intended release state;
5. known limitations above remain acceptable;
6. the project owner deliberately selects a software license or explicitly chooses to keep the repository without one.
