# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, rate limiting, usage budgets, sensitive-data controls, prompt-injection controls, tool exposure and execution authorization, provider routing, audit logging, observability, and security evaluation around upstream model use.

## Cost policy

This project is maintained under a **zero-cost-by-default** constraint.

Normal development, verification, release packaging, and portfolio completion do not require an AWS account, paid cloud infrastructure, a billing method, or paid LLM calls.

The required verified path uses:

- local Python development;
- Docker and Docker Compose;
- local kind Kubernetes;
- GitHub Actions within the account's included allowance;
- GitHub Container Registry for the public release image;
- the deterministic mock provider for routine runtime verification.

The AWS Terraform/Kubernetes implementation is an **optional paid reference architecture**. It may remain un-applied and runtime-untested indefinitely without blocking the roadmap.

See `docs/cost-policy.md`.

## Current capabilities

Implemented:

- FastAPI `/health`, `/metrics`, and OpenAI-style `/v1/chat/completions`
- deterministic non-network mock provider plus OpenAI and OpenRouter providers
- structured high-entropy gateway API keys with SHA-256 verification
- PostgreSQL-backed client/key registry, immediate key revocation, and atomic rotation
- deployment-wide model ceiling plus per-client model grants
- Redis/Valkey-backed distributed per-client rate limiting
- per-client UTC-day token/cost budgets with PostgreSQL persistence
- structured and local semantic/contextual PII detection with redact/deny policies
- deterministic prompt-injection audit/deny/off policies and versioned benchmark
- synthetic system-prompt leakage evaluation with disposable canaries
- least-privilege function-tool exposure authorization
- execution-time tool authorization with authoritative JSON Schemas, risk labels, short-lived signed tickets, and distributed one-time replay protection
- versioned named security-policy profiles
- structured one-line JSON security audit events with request/client attribution and sensitive-data exclusions
- Prometheus metrics with bounded labels and OpenTelemetry OTLP/HTTP tracing
- hardened Dockerized local stack with PostgreSQL, Redis, Prometheus, and OpenTelemetry Collector
- two-replica Kubernetes gateway deployment with backend-aware readiness, explicit migration Job, PodDisruptionBudget, and hardened pod security
- local kind workflow that verifies shared Redis/PostgreSQL security state across different gateway replicas
- zero-cost GitHub Container Registry release workflow using the repository `GITHUB_TOKEN`
- Terraform AWS reference foundation for protected remote state, VPC networking, private EKS, ECR, encrypted RDS PostgreSQL, IAM-authenticated Valkey, KMS, Secrets Manager, and EKS Pod Identity
- guarded optional AWS deployment workflow with separate gateway/migration Pod Identities, Secrets Manager runtime loading, least-privilege PostgreSQL runtime role provisioning, immutable ECR images, and private-by-default Kubernetes services
- GitHub Actions gates for pytest, both security benchmarks, Docker/Compose and deployment shell syntax, Kubernetes local/cloud manifest schemas, and Terraform validation

The next required milestone is **production/adversarial hardening**. Paid AWS runtime verification is optional and is not a prerequisite.

## Security request path

```text
Client
  |
  | sag_<key_id>_<secret>
  v
Secure AI Gateway
  |
  +-- PostgreSQL client/key lookup
  +-- constant-time key-hash verification
  +-- named client security profile
  +-- function-tool exposure authorization
  +-- Redis/Valkey per-client rate limit
  +-- deployment-wide + client model authorization
  +-- structured + semantic PII controls
  +-- prompt-injection inspection
  +-- PostgreSQL daily token/cost accounting
  +-- structured audit + bounded telemetry
  +-- provider routing
  |
  v
OpenRouter / OpenAI / other provider
  |
  | untrusted tool_call, if any
  v
Secure AI Gateway
  |
  +-- current client tool grant
  +-- authoritative schema fingerprint
  +-- exact argument JSON Schema validation
  +-- risk classification
  +-- short-lived signed execution ticket
  v
Client / executor
  |
  | POST /v1/tool-executions/authorize
  v
Execution-time re-authentication + one-time replay claim
```

A model-generated tool call is never treated as authorization to execute a side effect. The gateway currently authorizes execution but does not itself implement external side-effecting tools.

## Local configuration

Create ignored local configuration from the tracked examples:

```bash
cp .env.example .env
cp config/security-policies.example.json config/security-policies.json
```

Routine free verification should use the mock provider. A live OpenRouter/OpenAI configuration is optional and may incur provider charges depending on the selected provider/model.

Never commit `.env`, `.client.env`, `.k8s-client.env`, `.aws-client.env`, Terraform state/private variable files, or raw provider/gateway credentials.

Validate local policy with:

```bash
set -a
source .env
set +a
python -m app.policy_cli validate
```

## Dockerized local stack

Build and start the gateway plus shared state and observability services:

```bash
docker compose up -d --build
docker compose ps
curl -i http://127.0.0.1:8000/health
```

Prometheus is bound locally at `127.0.0.1:9090`; OTLP/HTTP is bound at `127.0.0.1:4318`. The gateway runs non-root with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and a bounded writable `/tmp`.

For routine shutdown, preserve persistent data:

```bash
docker compose down
```

Do not use `docker compose down -v` unless intentionally deleting persistent PostgreSQL, Redis, and Prometheus data.

See `docs/docker.md` and `docs/observability.md`.

## Kubernetes

The Kustomize deployment runs two gateway replicas behind `sag-gateway`. PostgreSQL and Redis remain shared sources of truth, so authentication, rate limits, usage budgets, and execution-ticket replay state are not replica-local.

The gateway Deployment includes:

- non-root UID/GID 10001;
- read-only root filesystem;
- `RuntimeDefault` seccomp;
- no privilege escalation;
- all Linux capabilities dropped;
- disabled service-account token automount;
- startup/liveness checks on `/health`;
- backend-aware readiness via `python -m app.readiness`;
- rolling updates and a PodDisruptionBudget;
- explicit resource requests/limits.

Kubernetes replicas set `SAG_RUN_MIGRATIONS=false`; schema changes are owned by the separate `k8s/migration` Job.

For the authoritative free runtime verification:

```bash
bash scripts/k8s-local-up.sh
bash scripts/k8s-verify.sh
```

The verification script sends requests directly to two different gateway pods and verifies shared Redis rate-limit state, shared PostgreSQL usage state, 32-character trace IDs, two healthy Prometheus targets, and OTLP tracing.

The local Kubernetes stack deliberately uses the mock provider, so this verification makes no real LLM provider call.

See `docs/kubernetes.md`.

## Zero-cost container release

`.github/workflows/release.yml` builds, smoke-tests, and publishes the container through GitHub Actions to:

```text
ghcr.io/joshuabisdorf/secure-ai-gateway
```

The workflow publishes an immutable `sha-*` image. A `v*` Git tag also publishes the matching version tag.

It uses only:

```yaml
permissions:
  contents: read
  packages: write
```

No AWS credentials, Docker Hub credentials, provider API keys, or Terraform apply are involved.

Because this repository is public and the image is published through the repository workflow, the release path is designed to use public GitHub Packages rather than paid hosting infrastructure.

See `docs/cloud-deployment.md` and `docs/cost-policy.md`.

## Terraform

The Terraform AWS reference is split into two roots:

```text
terraform/bootstrap  protected S3/KMS remote-state foundation
terraform/aws        optional application cloud infrastructure
```

The AWS root defines a VPC with public/private/isolated data subnets, NAT egress, a private-by-default EKS control plane, managed worker nodes, ECR, KMS, encrypted RDS PostgreSQL, TLS/IAM-authenticated ElastiCache Valkey, Secrets Manager containers, and separate EKS Pod Identity roles for gateway runtime and database migration.

Terraform validation is side-effect free:

```bash
terraform fmt -check -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate
```

This reference architecture is statically validated in CI but is not required to be applied. Do not run `terraform apply` merely to validate the repository.

See `docs/terraform.md`.

## Optional AWS reference deployment

The optional AWS cloud workflow keeps the gateway private by default and creates no Kubernetes Ingress or public `LoadBalancer`.

The architecture supports:

- TLS plus ElastiCache IAM authentication;
- separate runtime and migration Pod Identity roles;
- an RDS administrative migration identity and restricted `sag_runtime` login;
- Secrets Manager runtime loading;
- ECR image publishing;
- private Prometheus and OTLP services;
- cross-replica AWS runtime verification.

This path can create billable AWS services and is **not part of the required zero-cost project path**.

Any AWS apply operation requires both explicit guards:

```bash
SAG_ALLOW_BILLABLE_AWS=YES
SAG_CONFIRM_AWS_APPLY=YES
```

The deployment phase also requires `SAG_ALLOW_BILLABLE_AWS=YES` because it assumes a running paid environment.

The non-mutating AWS preflight remains available for someone who intentionally wants to inspect the design against a real AWS account, but AWS signup/authentication is not a project prerequisite.

See `docs/cloud-deployment.md`.

## Observability

Prometheus exposes bounded metric families including:

```text
sag_http_requests_total
sag_http_request_duration_seconds
sag_provider_requests_total
sag_provider_request_duration_seconds
sag_security_decisions_total
sag_pii_findings_total
sag_prompt_injection_findings_total
sag_tool_authorization_decisions_total
sag_usage_tokens_total
sag_usage_cost_usd_total
sag_backend_failures_total
```

Metric labels deliberately exclude client IDs, API-key IDs, request IDs, model names, tool names, prompt content, PII values, execution tickets, tool arguments, and results. OpenTelemetry traces likewise contain only bounded routing/provider metadata plus request correlation metadata, not prompt/response bodies or credentials.

See `docs/observability.md`.

## Security evaluation

Prompt-injection regression gate:

```bash
python -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
```

The version-1 curated baseline is:

```text
TP=36  FP=7  TN=13  FN=10
precision=0.8372
recall=0.7826
false_positive_rate=0.3500
false_negative_rate=0.2174
```

Semantic PII regression gate:

```bash
python -m app.evals.semantic_pii_benchmark --enforce-baseline --show-errors
```

The introduced version-1 curated baseline is:

```text
TP=32  FP=0  TN=20  FN=0
precision=1.0000
recall=1.0000
false_positive_rate=0.0000
false_negative_rate=0.0000
```

These are scoped regression datasets, not estimates of real-world production accuracy.

Synthetic system-prompt leakage evaluation:

```bash
python -m app.evals.system_prompt_leakage --live --model openrouter/free
```

System prompts are not treated as a secrecy or authorization boundary.

## Continuous integration

GitHub Actions runs six independent CI gates:

```text
Pytest
Prompt-injection benchmark
Semantic PII benchmark
Docker build
Kubernetes manifests
Terraform
```

The Kubernetes gate renders both `k8s/ci` and `k8s/cloud`, rejects tracked `Secret` objects, and schema-validates both deployment targets. The Docker gate checks optional AWS deployment shell syntax before building the image. The Terraform gate enforces formatting and validates both Terraform roots with remote backends disabled. CI receives no AWS credentials and does not create cloud resources.

The separate `Release container` workflow handles the zero-cost GHCR publishing path and has only package-write permission in addition to repository read access.

Run the main equivalent checks locally with:

```bash
pytest -q
python -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
python -m app.evals.semantic_pii_benchmark --enforce-baseline --show-errors
docker compose config --quiet
docker build --tag secure-ai-gateway:ci .
kubectl kustomize k8s/ci >/tmp/sag-kubernetes-rendered.yaml
kubectl kustomize k8s/cloud >/tmp/sag-cloud-kubernetes-rendered.yaml
terraform fmt -check -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate
```

See `docs/continuous-integration.md`.

## Repository layout

```text
Secure-AI-Gateway/
├── .github/workflows/
│   ├── ci.yml
│   └── release.yml
├── app/
│   ├── aws_migrate.py
│   ├── aws_runtime.py
│   ├── evals/
│   ├── policies/
│   ├── providers/
│   ├── readiness.py
│   ├── redis_client.py
│   ├── redis_replay.py
│   ├── semantic_pii.py
│   ├── tool_execution.py
│   └── ...
├── config/
├── db/migrations/
├── docker/entrypoint.sh
├── docs/
│   ├── cloud-deployment.md
│   ├── continuous-integration.md
│   ├── cost-policy.md
│   ├── docker.md
│   ├── kubernetes.md
│   ├── observability.md
│   ├── terraform.md
│   └── ...
├── evals/datasets/
├── k8s/
│   ├── base/
│   ├── ci/
│   ├── cloud/
│   ├── local/
│   └── migration/
├── observability/
├── scripts/
│   ├── aws-cloud-deploy.sh
│   ├── aws-cloud-preflight.sh
│   ├── aws-cloud-verify.sh
│   ├── k8s-local-up.sh
│   └── k8s-verify.sh
├── terraform/
│   ├── aws/
│   └── bootstrap/
├── tests/
├── Dockerfile
├── compose.yaml
└── pyproject.toml
```

## Roadmap

### Gateway foundation

- [x] FastAPI gateway
- [x] OpenAI-compatible chat-completions route
- [x] provider abstraction
- [x] mock provider
- [x] OpenAI provider
- [x] OpenRouter provider

### Core security controls

- [x] per-client identities and high-entropy API keys
- [x] hashed API-key verification
- [x] PostgreSQL persistent client/key registry
- [x] key revocation and atomic rotation
- [x] global/per-client model authorization
- [x] Redis/Valkey-backed distributed rate limiting
- [x] daily token/cost budgets
- [x] persistent PostgreSQL usage accounting
- [x] structured audit logging and request correlation
- [x] versioned configurable security-policy profiles

### LLM / agent security controls

- [x] structured PII detection/redaction
- [x] semantic PII detection/evaluation
- [x] deterministic prompt-injection detection
- [x] system-prompt leakage tests
- [x] least-privilege function-tool exposure authorization
- [x] execution-time tool authorization

### Evaluation and infrastructure

- [x] versioned adversarial prompt benchmark
- [x] versioned semantic PII benchmark
- [x] Dockerized gateway
- [x] GitHub Actions CI
- [x] OpenTelemetry / Prometheus
- [x] Kubernetes
- [x] Terraform reference architecture
- [x] zero-cost container release workflow
- [x] optional AWS deployment design (reference-only; paid runtime verification not required)
- [ ] production/adversarial hardening

## Function documentation convention

Project functions use RME-style docstrings:

- **Requires** — conditions that must hold before execution
- **Modifies** — state/resources changed
- **Effects** — externally visible side effects
- **Inputs** — function inputs
- **Outputs** — returned or produced outputs

## License

No license has been selected yet.
