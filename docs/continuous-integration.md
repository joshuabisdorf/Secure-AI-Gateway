# Continuous integration

Secure AI Gateway uses GitHub Actions to enforce deterministic security, infrastructure, deployment-artifact, and build gates on every push to `main` and on every pull request.

Workflow:

```text
.github/workflows/ci.yml
```

The workflow also supports manual `workflow_dispatch` runs.

## Security posture

CI intentionally does not require provider, PostgreSQL, Redis/Valkey, gateway-client, telemetry, Kubernetes runtime, or AWS credentials.

The test suite forces the mock provider plus in-memory rate-limit, usage-accounting, and tool-replay backends, and explicitly disables OTLP export. AWS runtime tests use injected fake SDK credentials/Secrets Manager responses rather than contacting AWS. The prompt-injection benchmark and semantic PII benchmark are fully offline. The Docker job validates Compose configuration, validates cloud-deployment shell syntax, and builds the production image but does not start the stack, push to ECR, or make provider calls. The Kubernetes job renders and schema-validates both the CI/local-style target and the AWS cloud target without creating a cluster or generating runtime Secret objects. The Terraform job initializes provider schemas with remote backends disabled and performs format/configuration validation only; it never runs `plan` or `apply` and receives no AWS credentials.

The workflow grants only:

```yaml
permissions:
  contents: read
```

No write permission is required for these gates.

## Gates

### Pytest

The `Pytest` job installs the project with development dependencies under Python 3.13 and runs:

```bash
pytest -q
```

This covers authentication, policy enforcement, rate limiting, usage budgets, structured and semantic PII handling, prompt-injection detection, system-prompt leakage evaluation logic, security-policy profiles, exposure/execution-time tool authorization, provider normalization, observability privacy/cardinality behavior, backend-aware Kubernetes readiness, portable Redis/Valkey rate limiting, ElastiCache IAM token construction, and AWS runtime secret mapping without calling real upstream providers, telemetry collectors, or AWS APIs.

### Prompt-injection benchmark

The `Prompt-injection benchmark` job runs:

```bash
python -m app.evals.prompt_injection_benchmark \
  --enforce-baseline \
  --show-errors
```

It fails when the committed detector drops below the versioned benchmark thresholds for precision or recall, or exceeds the maximum benchmark false-positive rate. Error output includes case IDs only; prompt bodies are not printed.

### Semantic PII benchmark

The `Semantic PII benchmark` job runs:

```bash
python -m app.evals.semantic_pii_benchmark \
  --enforce-baseline \
  --show-errors
```

It evaluates the local semantic/contextual PII analyzer against the versioned dataset and fails on precision, recall, or false-positive-rate regression. Output contains aggregate metrics, category names, and optional case IDs, not benchmark text or detected values.

Both benchmark gates are curated regression tests. Their passing thresholds and measured results are not estimates of production accuracy.

### Docker build

The `Docker build` job first validates the full Compose graph and deployment scripts:

```bash
docker compose config --quiet
bash -n \
  scripts/aws-cloud-preflight.sh \
  scripts/aws-cloud-deploy.sh \
  scripts/aws-cloud-verify.sh
```

It then runs a clean image build from the committed Dockerfile:

```bash
docker build --tag secure-ai-gateway:ci .
```

This validates that the gateway image, AWS SDK runtime dependency, Prometheus/collector service configuration references, pinned local semantic PII model, and cloud shell entrypoints remain reproducible independently of a developer workstation. CI does not start or push the image and requires no registry credentials.

### Kubernetes manifests

The `Kubernetes manifests` job installs Kubernetes 1.37 `kubectl` and renders both deployment targets:

```bash
kubectl kustomize k8s/ci > rendered-kubernetes.yaml
kubectl kustomize k8s/cloud > rendered-cloud-kubernetes.yaml
```

It rejects tracked `Secret` objects in either rendered target and validates both outputs with kubeconform strict schema checking and Kubernetes 1.37 compatibility checking.

The CI target includes the local PostgreSQL, Redis, Prometheus, OpenTelemetry Collector, two-replica gateway Deployment, PodDisruptionBudget, configuration objects, and dedicated database migration Job. The cloud target instead expects managed RDS/Valkey endpoints and AWS Pod Identity, keeps gateway/metrics/OTLP Services internal, and uses a dedicated cloud migration identity.

Runtime credentials are not generated in CI. The cloud deployment workflow resolves Secrets Manager values at runtime after infrastructure has been explicitly applied. Cross-replica behavior is exercised by `scripts/k8s-verify.sh` for kind and `scripts/aws-cloud-verify.sh` after an actual AWS deployment.

### Terraform

The `Terraform` job installs Terraform 1.16.2 and checks both Terraform roots:

```bash
terraform fmt -check -diff -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate -no-color
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate -no-color
```

The bootstrap root defines the protected S3/KMS state foundation. The AWS environment root defines the VPC, private EKS cluster and managed nodes, ECR, KMS, Secrets Manager containers, RDS PostgreSQL, IAM-authenticated Valkey, and separate EKS Pod Identity roles for gateway runtime and database migration.

`-backend=false` prevents CI from contacting or mutating the remote Terraform state backend. `terraform validate` checks HCL and the downloaded AWS provider schema but does not prove that a selected AWS account has quota/capacity for the resources or that every chosen engine/instance version is available in every Region. Those account/Region checks belong to the authenticated deployment preflight and plan.

CI deliberately does not run `terraform plan` or `terraform apply`. The local cloud workflow uses the operator's current short-lived/role-based AWS CLI identity; a future CI/CD promotion workflow should use short-lived GitHub OIDC federation to an explicitly scoped AWS role rather than stored long-lived AWS access keys.

## Concurrency

The workflow cancels an older in-progress run when a newer commit arrives for the same workflow/ref. This prevents obsolete CI work from consuming runner time while preserving independent runs across branches and pull requests.

## Dependency actions

The workflow uses current major releases of the checkout, Python setup, kubectl setup, Kubernetes lint, and Terraform setup actions. The production/adversarial-hardening milestone can pin action references to immutable full commit SHAs and automate controlled updates.

## Branch protection

Once the repository development flow uses pull requests consistently, these six jobs are intended to become required status checks before merging:

```text
Pytest
Prompt-injection benchmark
Semantic PII benchmark
Docker build
Kubernetes manifests
Terraform
```

Branch-protection policy is repository administration, not application runtime behavior, and should be configured separately from the gateway code.
