# Final security review

This document records the final security review for the first stable release.
It describes implemented controls and accepted residual risk. It is not a
formal verification, penetration-test report, or production certification.

## Review scope

The review walks the trust boundaries in `docs/architecture.md` and re-checks
authentication, authorization, secret handling, failure behavior, telemetry,
replay protection, concurrency, and resource exhaustion. The reviewed release
path is the zero-cost local/CI path. The AWS architecture remains reference-only
unless an operator deliberately deploys and tests it.

## Trust-boundary review

### Client to gateway

Attacker-controlled input includes the authorization header, request ID, HTTP
body framing, JSON shape, model name, messages, tool declarations, tool-choice
metadata, and execution-authorization requests.

The boundary applies a body-size limit before application parsing. Protected
endpoints authenticate structured gateway API keys against PostgreSQL-backed
records. Only one-way key digests are stored. Digest comparison is
constant-time after the public key ID selects the candidate record.

Authentication establishes client and key identity. It does not by itself
authorize models, tools, budgets, or executions. Those decisions are separate
policy checks performed after authentication.

### Gateway to provider

Provider credentials are gateway-side secrets and are never accepted from a
client request. Before forwarding a chat request, the gateway applies the
distributed rate limit, model authorization, PII policy, prompt-injection
policy, persistent usage-budget check, and tool-exposure policy.

PII redaction produces a copied provider request. The caller's request object is
not treated as trusted mutable policy state.

Provider transport and normalization failures are returned as sanitized gateway
errors. A provider response is not fabricated when required usage data is
missing.

### Provider to gateway

Provider output is untrusted data. The gateway does not treat generated text,
tool names, tool arguments, usage values, or provider identifiers as authority.

Returned tool calls must match current client authorization and the
authoritative execution registry. Arguments are parsed as JSON and validated
against the authoritative JSON Schema. The request-declared schema must match
the authoritative schema fingerprint before a ticket is issued.

### Gateway to downstream executor

A model tool proposal is not permission to perform a side effect. The gateway
issues a short-lived HMAC-SHA256 execution ticket only after validating the
proposal.

The ticket binds the execution ID, client ID, key ID, source request ID,
provider tool-call ID, tool name, exact argument hash, authoritative schema
hash, risk class, and expiry.

The executor must independently call
`POST /v1/tool-executions/authorize`. The gateway re-authenticates the caller,
verifies canonical ticket syntax, signature, expiry, identity, arguments,
schema, and risk, re-checks current tool policy, then atomically consumes the
execution ID. A stale or replayed ticket cannot become authority.

### Gateway to shared state

PostgreSQL is authoritative for gateway client/key identity and daily usage
accounting. Redis/Valkey is authoritative for distributed request-rate state and
one-time execution claims.

Loss of either required backend fails closed for the decision that depends on
it. PostgreSQL connection-pool waits are bounded. Redis replay claims and rate
limits are atomic across replicas.

### Gateway to telemetry

Audit events, Prometheus metrics, and OpenTelemetry traces receive bounded
metadata rather than request content. Prompts, raw PII, API keys, provider
credentials, raw tool arguments/results, and execution-ticket contents are
excluded.

Telemetry export is deliberately not an authorization dependency. A telemetry
failure does not grant access or bypass policy.

### Deployment and supply chain

The local Kubernetes namespace enforces the `restricted` Pod Security profile.
The local and cloud overlays start from ingress/egress default deny and add only
required paths. The gateway runs non-root with a read-only root filesystem,
dropped capabilities, `RuntimeDefault` seccomp, and explicit resource bounds.

The release build uses the exact runtime dependency lock. Direct downloaded
artifacts keep SHA-256 pins. Third-party GitHub Actions use immutable commit
SHAs. The public container workflow produces an immutable commit tag, resolves
the OCI digest, generates an SPDX image SBOM, and creates build-provenance and
SBOM attestations.

## Secret inventory and storage

The stable release has the following secret classes.

| Secret | Storage / handling |
| --- | --- |
| Raw gateway client API key | Returned only to an explicit exclusive `0600` secret file; PostgreSQL stores only its SHA-256 digest |
| Provider API key | Local ignored environment; optional AWS Secrets Manager runtime secret |
| Tool-execution HMAC key | Local ignored environment; optional AWS Secrets Manager runtime secret |
| PostgreSQL credentials | Local ignored environment; optional AWS Secrets Manager / RDS-managed secret |
| Redis password, when used | Local ignored environment or deployment secret source; not committed |
| ElastiCache IAM token | Short-lived AWS SDK credential flow; generated at runtime, not committed |
| EKS Pod Identity token | Short-lived runtime-mounted credential material managed by the platform |
| GitHub release token | Ephemeral repository `GITHUB_TOKEN` provided by Actions |
| Terraform state credentials | Operator/AWS credential chain; never committed to the repository |

Local kind creates `sag-runtime-secrets` from ignored local environment
configuration. That Kubernetes Secret is runtime-generated and is not committed.
Kubernetes Secret storage is not application-level encryption and relies on the
cluster's storage and access controls.

The cloud overlay does not commit Kubernetes Secret objects for runtime
credentials. Gateway and migration service accounts use separate EKS Pod
Identity roles and fetch the specific Secrets Manager values they require.

## Attacker-controlled and untrusted fields

The review treats these as attacker-controlled or untrusted:

- every client-supplied HTTP header and request JSON field;
- caller-provided request IDs;
- model names, messages, tool declarations, and tool choice;
- request-provided tool schemas;
- execution-authorization request bodies and ticket text;
- provider response bodies, usage fields, model names, and tool proposals;
- model-generated tool IDs, names, arguments, and text;
- local policy/configuration files when an operator has modified them;
- backend availability, timeout, connection exhaustion, and partial failure;
- deployment traffic arriving at any network path exposed by the operator.

Infrastructure-generated transport metadata is not automatically trusted for
authorization unless a documented deployment control explicitly establishes
that trust.

## Authentication and authorization separation

Authentication resolves an active gateway key to `client_id` and `key_id`.
Authorization remains independent:

1. model policy decides whether the authenticated client may request a model;
2. tool-exposure policy decides which function tools may be shown to the model;
3. the authoritative execution registry decides schema and risk metadata;
4. execution authorization re-checks identity, current policy, ticket binding,
   and replay state at the time of execution;
5. usage and rate policy independently limit otherwise authenticated requests.

Revoking a key prevents future authentication. Removing a tool grant prevents a
previously issued but not-yet-consumed ticket from being authorized.

## Fail-open and fail-closed review

| Dependency or control | Failure behavior |
| --- | --- |
| Client/key registry | Authentication unavailable; protected request denied |
| Security-policy load | Dependent policy decision unavailable; request denied |
| Redis rate limiter | Chat request returns `503` |
| PostgreSQL usage ledger | Chat request returns `503` |
| Usage data required by budget | Provider success is not returned; gateway returns `502` |
| Tool registry/signing key | Ticket issuance/authorization unavailable |
| Tool replay store | Execution authorization returns `503` |
| Malformed/expired/forged ticket | Generic execution denial |
| Provider timeout/malformed response | Sanitized upstream failure |
| Oversized request body | `413` before FastAPI body parsing |
| Telemetry exporter | Request authorization remains independent |
| Prometheus collection | No additional access is granted |
| Optional AWS reference path | Not part of the required release gate |

The resilience CI path verifies Redis and PostgreSQL fail closed and verifies
that an OpenTelemetry Collector outage does not become an authorization
dependency.

## Audit, metric, and trace leakage review

Audit emission uses an explicit metadata field list. There is no generic
serialization of request or response objects into the audit stream.

Prometheus labels are bounded and avoid client IDs, key IDs, request IDs, model
names, arbitrary tool names, prompts, and reasons. Known PII categories and
known prompt-injection indicators are reduced to bounded enumerations.

OpenTelemetry spans record route/method/status and bounded provider metadata.
Trace context may be extracted from incoming standard headers, but arbitrary
headers and request bodies are not recorded.

M10 regression coverage includes sentinel values proving that raw API keys,
execution tokens, and raw tool arguments do not appear in audit output.

## Replay, concurrency, and resource-exhaustion review

Execution IDs are claimed atomically and once. Real Redis concurrency testing
races independent replay-store instances against the same ID and requires
exactly one winner.

Distributed rate-limit contention is exercised against real Redis. Persistent
usage updates are exercised concurrently against PostgreSQL. Key rotation and
revocation races are checked for fail-closed active-key outcomes.

Request bodies are bounded before parsing. Uvicorn has bounded concurrency and
keep-alive configuration. Per-client rate limits and daily usage budgets bound
application-level consumption. Ticket size and lifetime are bounded. Policy
files and telemetry label surfaces are bounded. PostgreSQL pool acquisition is
bounded.

Kubernetes workloads declare CPU/memory requests and limits. Live kind
verification checks the configured limits, replica replacement, bounded
concurrent load, restart state, and observed `OOMKilled` state.

These controls reduce denial-of-service risk but do not provide complete
Internet-scale DDoS protection.

## Kubernetes and network review

The namespace enforces `restricted` Pod Security rather than only warning.
Gateway, migration, local PostgreSQL, and local Redis workloads use hardened
security contexts appropriate to their images and storage needs.

Both local and cloud overlays install namespace-wide default-deny
NetworkPolicies. Local verification proves that an otherwise untrusted probe
cannot connect directly to Redis while the gateway's allowed Redis path works.

Cloud backend egress policy is rendered from Terraform private/data subnet
CIDRs. Terraform also enables EKS VPC CNI NetworkPolicy support and creates a
private Secrets Manager interface endpoint. Live provider egress is not opened
by default; an operator must add a deployment-specific controlled path.

Standard Kubernetes NetworkPolicy is IP/port policy, not application
authentication or portable FQDN policy.

## Supply-chain review

Release dependencies are exact-pinned in `requirements/release.lock`. The
direct spaCy model artifact is SHA-256 pinned. The build backend is
exact-pinned. The Docker build consumes the release lock before installing the
application without dependency resolution.

CI includes the clean release installation, Bandit, `pip-audit`, CodeQL,
CycloneDX dependency SBOM generation, Trivy image scanning, Kubernetes
validation, Terraform validation, repository/history preflight, and the project
style gate.

The release workflow creates GitHub build-provenance and SPDX SBOM attestations
for the immutable OCI digest. Attestations prove artifact/repository identity;
they do not prove that the artifact is vulnerability-free.

## Accepted residual risks for v1.0.0

- Prompt-injection detection is heuristic and the curated benchmark has visible
  false positives and false negatives.
- Semantic PII evaluation is a small curated regression corpus, not a
  population-wide accuracy estimate.
- Real LLM providers can change behavior independently of the gateway.
- The AWS architecture is statically validated and intentionally may remain
  unapplied under the zero-cost policy.
- No external side-effecting executor is implemented by this repository. The
  provided boundary is authorization for a separate executor.
- Public ingress, TLS termination, DDoS protection, WAF, DNS, and certificate
  rotation remain deployment responsibilities.
- Standard NetworkPolicy does not authenticate protocols or provide portable
  DNS-name egress policy.
- Local Kubernetes Secret confidentiality depends on cluster storage and RBAC.
- Repository rulesets, branch protection, and optional GitHub account security
  settings remain repository-administration controls.
- Known-vulnerability scanners and static analysis cannot identify every
  unknown vulnerability or malicious dependency.
- Failure injection does not simulate every partition, kernel failure,
  managed-service failover, or Byzantine condition.

## Release decision

The security review finds no known issue that requires intentionally
fail-opening an authorization boundary for the first stable release.

The `v1.0.0` tag should be created only after the intended release commit has
green CI, CodeQL, container scanning, repository preflight, project style,
kind/Terraform verification, and a successful immutable public GHCR release
artifact with verifiable attestations. The tagged workflow must also prove that
the version tag resolves to the reviewed immutable digest.
