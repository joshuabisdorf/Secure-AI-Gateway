# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, rate limiting, usage budgets, sensitive-data controls, prompt-injection controls, tool exposure and execution authorization, provider routing, audit logging, observability, and security evaluation around upstream model use.

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
- local kind workflow that verifies Redis/PostgreSQL security state across different gateway replicas
- Terraform AWS foundation for protected remote state, VPC networking, private EKS, ECR, encrypted RDS PostgreSQL, IAM-authenticated Valkey, KMS, Secrets Manager, and EKS Pod Identity
- guarded AWS deployment workflow with separate gateway/migration Pod Identities, Secrets Manager runtime loading, least-privilege PostgreSQL runtime role provisioning, immutable ECR image publishing, and private-by-default Kubernetes services
- GitHub Actions gates for pytest, both security benchmarks, Docker/Compose and deployment shell syntax, Kubernetes local/cloud manifest schemas, and Terraform validation

The AWS cloud deployment implementation is ready for account-specific preflight and live verification. The next milestone after a verified AWS deployment is **production/adversarial hardening**.

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

A typical OpenRouter development configuration uses:

```dotenv
SAG_PROVIDER=openrouter
SAG_CLIENT_REGISTRY_BACKEND=postgres
SAG_USAGE_LEDGER_BACKEND=postgres
SAG_RATE_LIMIT_BACKEND=redis
SAG_SEMANTIC_PII_BACKEND=spacy
SAG_TOOL_EXECUTION_REPLAY_BACKEND=redis
SAG_SECURITY_POLICY_FILE=config/security-policies.json
SAG_TOOL_EXECUTION_POLICY_FILE=config/tool-execution-policies.example.json
SAG_ALLOWED_MODELS=openrouter/free

POSTGRES_PASSWORD=sag_dev_password
DATABASE_URL=postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway
REDIS_URL=redis://127.0.0.1:6379/0

SAG_TOOL_EXECUTION_SIGNING_KEY=
OPENROUTER_API_KEY=
```

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

For local kind testing:

```bash
bash scripts/k8s-local-up.sh
bash scripts/k8s-verify.sh
```

The bootstrap script creates runtime Secret material from ignored `.env` and writes the generated Kubernetes client credential to ignored `.k8s-client.env`. The verification script sends requests directly to two different gateway pods and verifies shared Redis rate-limit state, shared PostgreSQL usage state, per-pod Prometheus targets, and OTLP tracing.

The local Kubernetes stack deliberately uses the mock provider, so this verification makes no real LLM provider call.

See `docs/kubernetes.md`.

## Terraform

The Terraform AWS foundation is split into two independent roots:

```text
terraform/bootstrap  protected S3/KMS remote-state foundation
terraform/aws        application cloud infrastructure
```

The AWS root defines a VPC with public/private/isolated data subnets, NAT egress, a private-by-default EKS control plane, managed worker nodes, ECR, KMS, encrypted RDS PostgreSQL, TLS/IAM-authenticated ElastiCache Valkey, Secrets Manager containers, and separate EKS Pod Identity roles for gateway runtime and database migration.

Runtime provider credentials and the tool-execution signing key are not accepted as Terraform variables. Terraform creates Secret containers only; runtime secret values are populated during the guarded cloud-deployment stage. RDS owns its generated administrative password in Secrets Manager. The migration identity can read that administrative secret to apply schema changes and provision a distinct restricted `sag_runtime` database login; gateway pods can read the runtime database secret but not the RDS administrative secret.

Terraform validation is side-effect free:

```bash
terraform fmt -check -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate
```

Do not run `terraform apply` merely to validate the repository: an apply creates AWS resources and can incur charges.

See `docs/terraform.md`.

## AWS cloud deployment

The cloud workflow keeps the gateway private by default: it creates no Kubernetes Ingress or public `LoadBalancer`. Gateway, Prometheus, and OTLP Services remain cluster-internal, and development verification uses `kubectl port-forward`.

Cloud Redis-compatible connections use TLS plus ElastiCache IAM authentication through the AWS SDK credential chain supplied by EKS Pod Identity. Rate limiting uses a portable atomic Lua operation that works across local Redis and managed Valkey; readiness and execution-ticket replay use the same shared client/authentication path.

Start with the non-mutating account preflight:

```bash
bash scripts/aws-cloud-preflight.sh
```

The preflight validates the active AWS identity, Terraform configuration, cloud Kubernetes render, state-bootstrap plan, EKS administrator-role configuration, and workstation/API access posture. It does **not** apply AWS resources.

The guarded apply/deploy phases are separate:

```text
bootstrap-plan   inspect S3/KMS state-foundation plan
bootstrap-apply  create state foundation (explicit confirmation required)
plan             inspect main AWS infrastructure plan
infra-apply      create billable AWS infrastructure (explicit confirmation required)
deploy           publish image, configure runtime, migrate DB, roll out gateway
```

Both apply phases require the local safety acknowledgement `SAG_CONFIRM_AWS_APPLY=YES`. In particular, `infra-apply` creates billable EKS/EC2/NAT/RDS/ElastiCache resources and should only be run after reviewing the authenticated plan.

After deployment:

```bash
bash scripts/aws-cloud-verify.sh
```

The verifier sends authenticated requests to two distinct gateway pods and fails unless shared Valkey quota decreases across replicas, shared RDS usage increases across replicas, both trace IDs are present, Prometheus sees two healthy gateway targets, OTLP export is observed, and both gateway replicas are ready. The initial cloud runtime deliberately uses the mock provider, so this verification does not make an upstream LLM call.

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

GitHub Actions runs six independent gates:

```text
Pytest
Prompt-injection benchmark
Semantic PII benchmark
Docker build
Kubernetes manifests
Terraform
```

The Kubernetes gate renders both `k8s/ci` and `k8s/cloud`, rejects tracked `Secret` objects, and schema-validates both deployment targets. The Docker gate checks cloud deployment shell syntax before building the image. The Terraform gate enforces formatting and validates both Terraform roots with remote backends disabled. CI grants only `contents: read`, receives no AWS credentials, and does not create cloud resources.

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
├── .github/workflows/ci.yml
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
- [x] Terraform
- [ ] cloud deployment (implementation complete; live AWS verification pending)
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