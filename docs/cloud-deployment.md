# Cloud deployment and zero-cost release path

Secure AI Gateway is maintained under a zero-cost-by-default project policy. Normal development, verification, release packaging, and portfolio completion must not require paid cloud infrastructure or an AWS account.

The repository therefore has two distinct paths:

1. a **free verified path** built on local Docker/kind, GitHub Actions, and GitHub Container Registry;
2. an **optional paid AWS reference architecture** that is statically validated but does not need to be deployed.

See `docs/cost-policy.md` for the project-wide rule.

## Free verified path

The required path uses:

- local Python development;
- Docker and Docker Compose;
- local kind Kubernetes with two gateway replicas;
- PostgreSQL and Redis containers for distributed-state verification;
- Prometheus and OpenTelemetry Collector locally;
- deterministic mock-provider requests;
- GitHub Actions for CI;
- GitHub Container Registry for public release images published from this public repository.

No AWS account, cloud billing profile, LLM provider credential, or paid hosting service is required for this path.

### Container release

`.github/workflows/release.yml` builds the gateway container on GitHub-hosted Actions, smoke-tests the built image, and publishes an immutable `sha-*` tag to:

```text
ghcr.io/joshuabisdorf/secure-ai-gateway
```

A release tag such as `v0.1.0` also publishes the corresponding lowercase version tag.

The workflow uses the repository `GITHUB_TOKEN` with only:

```yaml
permissions:
  contents: read
  packages: write
```

It does not use AWS credentials, Docker Hub credentials, provider API keys, or Terraform.

The image is linked to this repository through its OCI source metadata and the workflow publishing context.

### Free runtime verification

The local kind workflow remains the authoritative runtime verification path:

```bash
bash scripts/k8s-local-up.sh
bash scripts/k8s-verify.sh
```

It verifies:

- two independent gateway replicas;
- shared Redis rate-limit state;
- shared PostgreSQL usage state;
- 32-character trace IDs;
- two healthy Prometheus gateway targets;
- OpenTelemetry trace export;
- mock-provider operation without upstream provider spend.

This proves the distributed runtime behavior without requiring a hosted Kubernetes control plane.

## Optional AWS reference architecture

The following resources are retained as a production-style architecture/reference implementation:

- `terraform/bootstrap`;
- `terraform/aws`;
- `k8s/cloud`;
- `scripts/aws-cloud-preflight.sh`;
- `scripts/aws-cloud-deploy.sh`;
- `scripts/aws-cloud-verify.sh`.

The AWS design includes:

- Amazon EKS for the two-replica gateway;
- ECR for immutable gateway images;
- RDS PostgreSQL for client/key and usage state;
- ElastiCache Valkey for distributed rate limiting and execution-ticket replay;
- KMS for encryption;
- Secrets Manager for runtime secrets;
- EKS Pod Identity for short-lived workload credentials;
- private-by-default Kubernetes services;
- a protected S3 Terraform backend.

This path demonstrates how the gateway would map onto managed AWS services, but deploying it is **not required** and should not be represented as runtime-verified unless someone deliberately chooses to pay for and test it.

## Runtime identities in the AWS reference

Two service accounts have separate Pod Identity roles.

`sag-gateway` can read only its runtime database/signing/provider secret containers and connect to the configured IAM-authenticated Valkey service.

`sag-migration` can read only the RDS administrative bootstrap secret and runtime database credential needed to apply schema changes and provision the restricted runtime database login.

Gateway pods cannot read the RDS master-user secret. The migration role does not receive provider-secret or Valkey runtime permissions.

## PostgreSQL privilege split

The AWS migration Job runs `python -m app.aws_migrate` with the RDS administrative credential. It applies the versioned migrations, then creates or rotates a restricted `sag_runtime` login with runtime data-plane grants.

The gateway Deployment resolves only the `sag_runtime` credential into `DATABASE_URL`, requires TLS, and does not own schema migrations.

## Valkey IAM authentication

Cloud Redis-compatible connections use `SAG_REDIS_AUTH_MODE=elasticache_iam` and a `rediss://` URL. The gateway uses the AWS SDK credential chain supplied by EKS Pod Identity to generate ElastiCache IAM authentication tokens.

The same Redis/Valkey client factory is used by:

- distributed rate limiting;
- execution-ticket replay protection;
- Kubernetes readiness checks.

The distributed limiter uses a portable one-key Lua transaction rather than Redis-version-specific commands.

## Secret handling

No Kubernetes `Secret` object is committed in the cloud overlay. Deployment-time configuration contains only endpoints, AWS region/resource identifiers, and Secrets Manager IDs.

Runtime secret values are loaded in memory immediately before the normal gateway entrypoint. They are not written into committed Kubernetes manifests.

The optional AWS verification path uses the mock provider, so it does not require a live LLM provider call.

## AWS preflight is optional

If someone deliberately wants to inspect the AWS design against a real AWS account, the non-mutating preflight is:

```bash
bash scripts/aws-cloud-preflight.sh
```

It may authenticate to AWS and generate Terraform plans, but it does not create AWS resources.

This step is **not** part of the required project setup and should be skipped when preserving the zero-cost constraint.

## Paid AWS apply guard

Any command capable of creating or modifying the optional AWS runtime must leave the free path explicitly.

Both environment variables are required for Terraform apply operations:

```bash
export SAG_ALLOW_BILLABLE_AWS=YES
export SAG_CONFIRM_AWS_APPLY=YES
```

`SAG_ALLOW_BILLABLE_AWS=YES` acknowledges that the operator is intentionally leaving the project's zero-cost path. `SAG_CONFIRM_AWS_APPLY=YES` confirms the specific Terraform apply action.

The deployment phase also requires `SAG_ALLOW_BILLABLE_AWS=YES` because it assumes an already-running billable AWS environment and may push/use billable services.

CI never sets either variable.

## Verification language

Use these terms precisely:

- **CI-verified**: exercised by GitHub Actions;
- **locally verified**: exercised on Docker/kind;
- **release-verified**: built, smoke-tested, and published by the GHCR release workflow;
- **AWS reference-only**: Terraform/Kubernetes design is statically validated, but no claim is made that paid AWS runtime behavior was executed.

The AWS reference may remain untested indefinitely without blocking the roadmap.
