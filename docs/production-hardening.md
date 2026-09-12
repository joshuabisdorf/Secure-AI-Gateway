# Production and adversarial hardening

This milestone hardens the zero-cost verified path of Secure AI Gateway without requiring paid infrastructure or upstream LLM calls.

## Implemented controls

### HTTP / ASGI boundary

- Requests are bounded before FastAPI/Pydantic parsing; oversized declared or streamed bodies receive HTTP 413.
- Ambiguous/multiple Content-Length values fail closed.
- API docs/OpenAPI routes are disabled by default and require an explicit opt-in.
- Responses receive `Cache-Control: no-store`, `Content-Security-Policy`, `Referrer-Policy`, `X-Content-Type-Options`, and `X-Frame-Options` protections.
- Production Uvicorn defaults cap concurrent connections, use a short keep-alive timeout, suppress the server header, and do not trust proxy forwarding headers by default.

### Tool execution boundary

- Model-generated tool calls remain untrusted output.
- Execution requires an HMAC-SHA256 ticket bound to client ID, key ID, request ID, tool-call ID, exact argument bytes, authoritative schema fingerprint, risk class, and expiry.
- Execution authorization re-checks current tool policy and authoritative schema before replay claim.
- Replay protection is one-time and distributed when Redis/Valkey is configured.
- HTTP authorization rejects non-canonical execution-ticket text before Base64 decoding; padding, whitespace, punctuation, empty/extra segments, and oversized encodings fail closed.

### Dependency and CI supply chain

- GitHub Actions are pinned to immutable commit SHAs rather than mutable major tags.
- CI includes Bandit static analysis and `pip-audit` dependency vulnerability checks.
- Dependabot monitors Python and GitHub Actions dependencies weekly.
- The public GHCR release workflow builds, smoke-tests, publishes, logs out, and verifies an anonymous pull.
- CI and release workflows use repository-scoped permissions and require no AWS credentials.

### Runtime containment

- The production container runs as non-root with a read-only root filesystem in Docker/Kubernetes deployments, drops Linux capabilities, and enables `no-new-privileges` / RuntimeDefault seccomp where applicable.
- Kubernetes gateway replicas use explicit resource requests/limits and a PodDisruptionBudget.
- Runtime Secret objects are not committed to Kubernetes manifests.
- Paid AWS deployment remains opt-in behind explicit billable-resource guards and is not required for project verification.

### Adversarial regression coverage

The test/benchmark suite covers, among other cases:

- malformed and revoked gateway credentials;
- unauthorized models and tools;
- prompt-injection regression cases;
- structured and semantic PII regression cases;
- request-body amplification / malformed request-boundary cases;
- execution-ticket replay, argument tampering, identity binding, schema substitution, policy revocation, and non-canonical token encodings;
- provider serialization ensuring gateway-only execution metadata does not leave the gateway;
- telemetry/audit exclusions for sensitive values;
- shared Redis/PostgreSQL behavior across two Kubernetes replicas.

## Residual risks / explicit non-guarantees

- Prompt-injection detection is heuristic and benchmark-scoped; sophisticated attacks may evade it or produce false positives.
- Semantic PII detection is not a guarantee that all sensitive information will be detected.
- System prompts are not secrets and are not authorization controls.
- The gateway authorizes external tool execution but does not execute side effects itself; downstream executors must enforce the authorization response and preserve least privilege.
- The optional AWS reference deployment is configuration/CI validated but is not required to be live deployed or operationally tested.
- No public ingress, DNS, TLS termination, WAF, DDoS service, or internet-facing production SLO is claimed.
- Disaster recovery, restore exercises, long-duration load tests, and multi-region failover are not claimed.
- Branch protection / repository rulesets depend on repository administration and are not enforced by application code.
- Passing Bandit, `pip-audit`, CI, benchmarks, and adversarial tests does not prove absence of vulnerabilities.

## Zero-cost verification boundary

The verified portfolio path remains free to the repository owner: local Docker/kind, mock-provider tests, GitHub Actions on the public repository, Terraform validation without apply, and public GHCR packaging. Paid services are optional reference targets and may remain untested.
