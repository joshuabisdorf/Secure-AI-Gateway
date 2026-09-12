# Continuous integration

Secure AI Gateway uses GitHub Actions to enforce deterministic security, infrastructure, and build gates on every push to `main` and on every pull request.

Workflow:

```text
.github/workflows/ci.yml
```

The workflow also supports manual `workflow_dispatch` runs.

## Security posture

CI intentionally does not require provider, PostgreSQL, Redis, gateway-client, telemetry, Kubernetes runtime, or AWS credentials.

The test suite forces the mock provider plus in-memory rate-limit, usage-accounting, and tool-replay backends, and explicitly disables OTLP export. The prompt-injection benchmark and semantic PII benchmark are fully offline. The semantic PII job runs the local spaCy model installed with the project; message text is not sent to a remote classifier. The Docker job validates Compose configuration and builds the production image but does not start the stack or make provider calls. The Kubernetes job renders and schema-validates manifests without creating a cluster or generating runtime Secret objects. The Terraform job initializes provider schemas with remote backends disabled and performs format/configuration validation only; it never runs `plan` or `apply` and receives no AWS credentials.

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

This covers authentication, policy enforcement, rate limiting, usage budgets, structured and semantic PII handling, prompt-injection detection, system-prompt leakage evaluation logic, security-policy profiles, exposure/execution-time tool authorization, provider normalization, observability privacy/cardinality behavior, and backend-aware Kubernetes readiness checks without calling real upstream providers or telemetry collectors.

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

The `Docker build` job first validates the full Compose graph:

```bash
docker compose config --quiet
```

It then runs a clean image build from the committed Dockerfile:

```bash
docker build --tag secure-ai-gateway:ci .
```

This validates that the gateway image, Prometheus/collector service configuration references, and pinned local semantic PII model remain reproducible independently of a developer workstation. CI does not start or push the images and requires no registry credentials.

### Kubernetes manifests

The `Kubernetes manifests` job installs Kubernetes 1.37 `kubectl`, renders the complete CI Kustomize target, rejects tracked Secret objects, and validates the rendered resources with kubeconform strict schema checking:

```bash
kubectl kustomize k8s/ci > rendered-kubernetes.yaml
```

The rendered target includes the local PostgreSQL, Redis, Prometheus, OpenTelemetry Collector, two-replica gateway Deployment, PodDisruptionBudget, configuration objects, and the dedicated database migration Job.

The job deliberately does not create `sag-runtime-secrets`. Runtime credentials are generated from ignored local/cloud secret configuration at deployment time. This gate validates manifest structure and Kubernetes API compatibility; cross-replica runtime behavior is exercised separately with the local kind workflow described in `docs/kubernetes.md`.

### Terraform

The `Terraform` job installs Terraform 1.16.2 and checks both Terraform roots:

```bash
terraform fmt -check -diff -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate -no-color
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate -no-color
```

The bootstrap root defines the protected S3/KMS state foundation. The AWS environment root defines the VPC, private EKS cluster and managed nodes, ECR, KMS, Secrets Manager containers, RDS PostgreSQL, IAM-authenticated Valkey, and EKS Pod Identity resources.

`-backend=false` prevents CI from contacting or mutating the remote Terraform state backend. `terraform validate` checks HCL and the downloaded AWS provider schema but does not prove that a selected AWS account has quota/capacity for the resources or that every chosen engine/instance version is available in every Region. Those account/Region checks belong to an authenticated deployment plan.

CI deliberately does not run `terraform plan` or `terraform apply`. A later cloud-deployment workflow should use short-lived GitHub OIDC federation to an explicitly scoped AWS role instead of stored long-lived AWS access keys.

## Concurrency

The workflow cancels an older in-progress run when a newer commit arrives for the same workflow/ref. This prevents obsolete CI work from consuming runner time while preserving independent runs across branches and pull requests.

## Dependency actions

The workflow uses current major releases of the checkout, Python setup, kubectl setup, Kubernetes lint, and Terraform setup actions. A later supply-chain-hardening milestone can pin action references to immutable full commit SHAs and automate controlled updates.

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
