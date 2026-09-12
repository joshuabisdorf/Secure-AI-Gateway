# Continuous integration

Secure AI Gateway uses GitHub Actions for deterministic security, application, infrastructure, demo, resilience, code-scanning, and release checks on `main` and pull requests.

Primary CI workflow:

```text
.github/workflows/ci.yml
```

Additional security/release workflows:

```text
.github/workflows/codeql.yml
.github/workflows/release.yml
```

## Zero-cost boundary

The required CI path does not need provider keys, AWS credentials, a Kubernetes cluster supplied by the user, or paid infrastructure. Unit/security tests use the deterministic mock provider and injected/in-memory substitutes where appropriate. Runtime demo/resilience jobs start only local Docker services on GitHub-hosted runners. Terraform runs only format/init-without-backend/validate. No required workflow runs `terraform apply`.

The release workflow publishes to public GHCR using the repository `GITHUB_TOKEN` and then verifies an anonymous pull. It does not use AWS, Docker Hub, or provider credentials.

## Permissions and supply-chain posture

The primary CI workflow grants only:

```yaml
permissions:
  contents: read
```

CodeQL receives only repository read plus `security-events: write` so analysis results can be uploaded to GitHub code scanning. The release workflow receives repository read plus package write.

Third-party GitHub Actions are pinned to immutable full commit SHAs. Dependabot proposes controlled Python, GitHub Actions, and Docker updates.

## Nine primary CI gates

### Pytest

```bash
pytest -q
```

Covers authentication, key lifecycle, model/policy enforcement, Redis/Valkey-compatible rate limiting, persistent usage logic, PII, prompt-injection detection, provider normalization, tool exposure/execution authorization, ticket replay/tampering/encoding, observability privacy/cardinality, readiness, and AWS-runtime helpers without contacting real cloud APIs.

### Security analysis

```bash
bandit -q -r app -ll -ii
python -m pip_audit --progress-spinner off --skip-editable
```

The same job generates a CycloneDX JSON dependency SBOM through `pip-audit` and parses it to verify the document is structurally usable. The generated SBOM is intentionally ephemeral so stale dependency inventories are not committed to source control.

### Prompt-injection benchmark

```bash
python -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
```

Fails when the versioned curated precision/recall/false-positive thresholds regress. The dataset is a regression corpus, not a production accuracy estimate.

### Semantic PII benchmark

```bash
python -m app.evals.semantic_pii_benchmark --enforce-baseline --show-errors
```

Fails on the versioned semantic PII baseline thresholds. The dataset is likewise a bounded regression corpus.

### Docker build

The job validates Compose, checks shell syntax for demo/resilience/kind/AWS helper scripts, verifies the developer Make task surface, checks the billable-AWS opt-in guards, and builds the production Dockerfile.

```bash
docker compose config --quiet
bash -n scripts/demo.sh scripts/resilience-smoke.sh \
  scripts/k8s-local-up.sh scripts/k8s-verify.sh \
  scripts/aws-cloud-preflight.sh scripts/aws-cloud-deploy.sh scripts/aws-cloud-verify.sh
make help
docker build --tag secure-ai-gateway:ci .
```

No image is pushed by this CI job.

### End-to-end demo

A fresh GitHub-hosted Ubuntu runner executes:

```bash
make demo
```

The demo starts Compose PostgreSQL, Redis, Prometheus, the OpenTelemetry Collector, and the gateway and uses only the deterministic mock provider. It creates a temporary PostgreSQL-backed client and verifies authenticated chat, persistent usage accounting, PII redaction, prompt-injection detection, execution-ticket issuance, execution-time authorization, replay denial, distributed rate limiting, and Prometheus metrics. The temporary key is revoked by script cleanup, and CI always runs `docker compose down` after the demo.

No AWS account, provider API key, or paid model call is involved.

### Resilience smoke

A separate clean runner executes:

```bash
make resilience
```

It verifies live Compose behavior under controlled outages:

- Redis stopped -> chat returns 503;
- Redis restored -> chat returns 200;
- PostgreSQL stopped -> authentication/chat returns 503;
- PostgreSQL restored -> chat returns 200;
- OpenTelemetry Collector stopped -> chat remains 200.

This makes the intended fail-closed security dependency boundary executable. CI cleanup stops the stack without deleting volumes.

### Kubernetes manifests

CI renders both `k8s/ci` and `k8s/cloud`, rejects rendered Kubernetes `Secret` objects, validates schemas strictly, and checks Kubernetes 1.37 compatibility. It does not create a cluster.

The free distributed-runtime proof remains the local kind workflow:

```bash
make kind-up
make kind-verify
```

### Terraform

```bash
terraform fmt -check -diff -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate -no-color
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate -no-color
```

Remote state is disabled in CI. No plan/apply or AWS credential is required.

## CodeQL

The separate CodeQL workflow analyzes Python on pushes, pull requests, manual dispatch, and a weekly schedule. `github/codeql-action` is pinned to an immutable commit corresponding to v4. The workflow uses the `security-extended` query suite and uploads results to GitHub code scanning.

Code scanning is available for public repositories on GitHub.com, so this does not add a paid repository requirement.

## Release container workflow

The release workflow runs on `main`, manual dispatch, and `v*` tags. It has only repository read and package write permission.

The workflow:

1. derives an immutable `sha-<commit>` image tag;
2. logs into GHCR with `GITHUB_TOKEN`;
3. builds the production image;
4. smoke-tests the built image;
5. publishes the immutable SHA image;
6. publishes a version tag for `v*` refs;
7. logs out of GHCR;
8. deletes the local image;
9. pulls the image anonymously;
10. smoke-tests the anonymously pulled image.

Target image:

```text
ghcr.io/joshuabisdorf/secure-ai-gateway
```

## Branch protection

The intended required status checks for a pull-request-based development flow are:

```text
Pytest
Security analysis
Prompt-injection benchmark
Semantic PII benchmark
Docker build
End-to-end demo
Resilience smoke
Kubernetes manifests
Terraform
CodeQL Python
```

Repository branch protection/rulesets are account-level administration settings; application code cannot enforce them. Their absence or presence should therefore be treated separately from the code-level security model.
