# Portfolio evidence

This page defines the evidence a reviewer should be able to inspect quickly without reconstructing the project history.

## Core evidence

| Evidence | What it demonstrates | Reproduction |
| --- | --- | --- |
| `make demo` | authenticated request path, usage accounting, PII redaction, prompt-injection detection, tool-ticket issuance, execution authorization, replay denial, rate limiting, Prometheus metrics | local Docker Compose, mock provider only |
| `make resilience` | Redis and PostgreSQL fail closed; telemetry outage does not become an authorization dependency | local Docker Compose, mock provider only |
| `make check` | unit/integration tests, security evaluation baselines, Bandit, dependency audit | local Python environment |
| `make kind-up && make kind-verify` | two gateway replicas sharing distributed state and telemetry | local kind cluster |
| `make terraform-validate` | formatting and static validity of both Terraform roots | local Terraform |
| `make preflight` | release/repository hygiene, required files, immutable action refs, credential/artifact checks | Git checkout only |

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

The exact numeric usage counters may differ because persistent development volumes are intentionally preserved. The PASS/FAIL security assertions are deterministic.

## CI evidence

The primary CI workflow separates failure domains into independent jobs:

- Pytest;
- security analysis;
- prompt-injection benchmark;
- semantic PII benchmark;
- Docker build/configuration validation;
- end-to-end portfolio demo;
- resilience smoke test;
- Kubernetes schema/compatibility validation;
- Terraform formatting/validation.

Additional workflows provide CodeQL analysis, scheduled/PR container vulnerability scanning, and public GHCR image publication with anonymous-pull verification.

For a portfolio capture, prefer one screenshot of the GitHub Actions run showing all green jobs rather than many individual job screenshots. The repository and logs remain the authoritative evidence.

## Security benchmark evidence

The versioned corpora are regression tests, not claims of universal detection quality. Current documented baselines are intentionally published with false-positive/false-negative counts so limitations remain visible.

For screenshots or a short demo recording, capture:

1. the README five-minute reviewer path;
2. one full successful `make demo` result;
3. the GitHub Actions job matrix;
4. Prometheus showing gateway request metrics;
5. one OpenTelemetry Collector trace/debug entry with no prompt or credential contents;
6. the execution-authorization sequence diagram;
7. the benchmark summary from the README.

Do not capture raw API keys, execution tickets, provider credentials, `.env` content, Terraform state, or raw sensitive request payloads.

## Release evidence

For `v1.0.0`, retain these verifiable facts in the repository/release page:

- release commit SHA;
- `v1.0.0` annotated tag;
- immutable `ghcr.io/joshuabisdorf/secure-ai-gateway:sha-<commit>` image;
- corresponding `:v1.0.0` image;
- green release workflow including anonymous pull verification;
- current `CHANGELOG.md` and `docs/release-checklist.md`;
- documented residual risks in `docs/security-review.md`.

## Evidence quality rule

Prefer reproducible commands and CI logs over manually curated screenshots. Screenshots are useful for fast portfolio review, but every screenshot should point back to a repository command, workflow, test, benchmark, or documented invariant that another reviewer can reproduce.
