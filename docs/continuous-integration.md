# Continuous integration

Secure AI Gateway uses GitHub Actions to enforce deterministic security, infrastructure, deployment-artifact, and build gates on every push to `main` and on every pull request.

The required CI workflow is:

```text
.github/workflows/ci.yml
```

The separate zero-cost container release workflow is:

```text
.github/workflows/release.yml
```

## Zero-cost boundary

CI and release are designed not to require project-specific paid cloud infrastructure.

The required CI workflow intentionally does not require provider, PostgreSQL, Redis/Valkey, gateway-client, telemetry, Kubernetes runtime, or AWS credentials. It never runs Terraform `plan` or `apply` and never deploys the optional AWS reference architecture.

The release workflow publishes the public container through GitHub Container Registry using the repository `GITHUB_TOKEN`. It does not use AWS credentials, Docker Hub credentials, provider API keys, or Terraform.

See `docs/cost-policy.md`.

## CI security posture

The test suite forces the mock provider plus in-memory rate-limit, usage-accounting, and tool-replay backends, and explicitly disables OTLP export. AWS runtime tests use injected fake SDK credentials/Secrets Manager responses rather than contacting AWS. The prompt-injection benchmark and semantic PII benchmark are fully offline.

The Docker job validates Compose configuration, validates optional AWS deployment shell syntax, verifies that the billable-AWS opt-in guard remains present, and builds the production image. It does not start paid infrastructure, push to ECR, or make provider calls.

The Kubernetes job renders and schema-validates both the local/CI target and the optional AWS cloud target without creating a cluster or generating runtime Secret objects.

The Terraform job initializes provider schemas with remote backends disabled and performs format/configuration validation only.

The CI workflow grants only:

```yaml
permissions:
  contents: read
```

No write permission is required for the six CI gates.

## CI gates

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

The `Docker build` job validates the full Compose graph and optional AWS deployment scripts:

```bash
docker compose config --quiet
bash -n \
  scripts/aws-cloud-preflight.sh \
  scripts/aws-cloud-deploy.sh \
  scripts/aws-cloud-verify.sh
```

It also fails if the optional paid deployment script no longer contains the explicit billable-AWS and apply-confirmation guards.

The job then runs a clean image build:

```bash
docker build --tag secure-ai-gateway:ci .
```

CI does not push the image and requires no registry credentials.

### Kubernetes manifests

The `Kubernetes manifests` job installs Kubernetes 1.37 `kubectl` and renders both deployment targets:

```bash
kubectl kustomize k8s/ci > rendered-kubernetes.yaml
kubectl kustomize k8s/cloud > rendered-cloud-kubernetes.yaml
```

It rejects tracked `Secret` objects in either rendered target and validates both outputs with strict kubeconform schema checking and Kubernetes 1.37 compatibility checking.

The CI target includes local PostgreSQL, Redis, Prometheus, OpenTelemetry Collector, two gateway replicas, a PodDisruptionBudget, configuration objects, and the dedicated database migration Job.

The cloud target is reference-only by default. It expects managed RDS/Valkey endpoints and AWS Pod Identity, keeps gateway/metrics/OTLP Services internal, and uses a dedicated cloud migration identity.

Runtime credentials are not generated in CI. Cross-replica runtime behavior is exercised for free with `scripts/k8s-verify.sh`. `scripts/aws-cloud-verify.sh` is available only if someone deliberately chooses to deploy the optional paid AWS reference architecture.

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

`-backend=false` prevents CI from contacting or mutating remote Terraform state. `terraform validate` checks HCL and the downloaded AWS provider schema; it does not claim that the optional paid AWS runtime was deployed or tested.

CI deliberately does not run `terraform plan` or `terraform apply`.

## Release container workflow

`Release container` runs on manual dispatch or a `v*` tag.

It has narrowly scoped permissions:

```yaml
permissions:
  contents: read
  packages: write
```

The workflow:

1. derives an immutable `sha-<commit>` image tag;
2. logs into GHCR using `GITHUB_TOKEN`;
3. builds the production Dockerfile;
4. smoke-tests the built image by importing the gateway application inside the container;
5. publishes the immutable image;
6. publishes a matching version tag for `v*` releases.

The target image is:

```text
ghcr.io/joshuabisdorf/secure-ai-gateway
```

This is the maintained zero-cost release path. If an account-level included GitHub quota is ever exhausted, usage should stop rather than enabling paid overages solely for this project.

## Concurrency

The CI workflow cancels an older in-progress run when a newer commit arrives for the same workflow/ref. Release publishing does not cancel an in-progress run for the same ref.

## Dependency actions

The workflows use current major releases of GitHub checkout, Python setup, kubectl setup, Kubernetes lint, and Terraform setup actions. The production/adversarial-hardening milestone can pin action references to immutable full commit SHAs and automate controlled updates.

## Branch protection

Once the repository development flow uses pull requests consistently, these six CI jobs are intended to become required status checks before merging:

```text
Pytest
Prompt-injection benchmark
Semantic PII benchmark
Docker build
Kubernetes manifests
Terraform
```

The release workflow is intentionally not a merge gate.
