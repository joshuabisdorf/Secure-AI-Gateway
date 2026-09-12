# Zero-cost project policy

Secure AI Gateway is developed and verified under a zero-cost-by-default constraint.

The project must not require the maintainer to purchase cloud services, add a billing method, or keep billable infrastructure running in order to complete normal development, CI, release, or portfolio verification.

## Required free path

The maintained and verified path uses only resources that can be used without project-specific cloud spend:

- local Python development;
- Docker and Docker Compose on the developer workstation;
- local kind Kubernetes;
- GitHub Actions within the account's included allowance;
- GitHub Container Registry for a public package published from this public repository;
- deterministic mock-provider requests for routine verification.

The public container release workflow uses the repository-scoped `GITHUB_TOKEN`; it does not require AWS credentials, Docker Hub credentials, provider credentials, or a paid deployment platform.

If a GitHub account-level included quota is exhausted, the workflow should be allowed to stop rather than enabling paid overage merely to keep this project running.

## Optional paid reference architecture

`terraform/aws`, `k8s/cloud`, and the `scripts/aws-cloud-*` scripts are retained as an optional production-style AWS reference architecture.

That architecture includes resources such as EKS, NAT Gateway, EC2 worker capacity, RDS PostgreSQL, ElastiCache Valkey, KMS, Secrets Manager, ECR, and S3. Some of those services are billable independently of workload traffic. Therefore:

- AWS deployment is not required to complete the project;
- AWS runtime verification is not required for a green repository;
- CI must never run Terraform `apply`;
- no AWS account or payment method is a project prerequisite;
- an untested optional paid component must be documented as such rather than represented as verified;
- paid AWS apply paths require explicit local opt-in guards.

## Cost fail-closed rule

Any script capable of creating the optional billable AWS environment must require both:

```text
SAG_ALLOW_BILLABLE_AWS=YES
SAG_CONFIRM_AWS_APPLY=YES
```

The first variable acknowledges that the command leaves the zero-cost project path. The second confirms the specific Terraform apply action.

Neither variable is a credential and neither may be set by CI.

## Verification claims

Repository documentation should distinguish these states:

- **CI-verified** — exercised by GitHub Actions;
- **locally verified** — exercised on Docker/kind without paid infrastructure;
- **release-verified** — a public GHCR image was built, smoke-tested, and published by the release workflow;
- **reference only** — designed and statically validated, but not deployed because deployment would create billable infrastructure.

The AWS Terraform/Kubernetes path may remain reference-only indefinitely without blocking the project roadmap.

## Provider costs

Routine automated verification uses the mock provider and must not require paid LLM API calls. Live OpenAI/OpenRouter/provider tests are optional and should only be run deliberately with user-supplied credentials and an understood provider billing policy.
