# Cloud deployment and zero-cost release path

Secure AI Gateway is maintained under a zero-cost-by-default project policy.
Normal development, verification, release packaging, and portfolio completion
do not require paid cloud infrastructure or an AWS account.

The repository has two separate paths:

1. a free verified path built on local Docker/kind, GitHub Actions, and GHCR;
2. an optional paid AWS reference architecture that is statically validated.

See `docs/cost-policy.md` for the project-wide rule.

## Free verified path

The required path uses:

- local Python development;
- Docker and Docker Compose;
- local kind Kubernetes with two gateway replicas;
- PostgreSQL and Redis containers for shared-state verification;
- Prometheus and OpenTelemetry Collector locally;
- deterministic mock-provider requests;
- GitHub Actions for CI;
- GitHub Container Registry for public release images.

No AWS account, cloud billing profile, LLM provider credential, or paid hosting
service is required.

### Container release

`.github/workflows/release.yml` builds the gateway container, smoke-tests it,
and publishes an immutable `sha-*` tag to:

```text
ghcr.io/joshuabisdorf/secure-ai-gateway
```

A stable tag such as `v1.0.0` publishes the corresponding version alias. The
workflow proves that the version alias resolves to the same immutable OCI digest
and verifies anonymous pulls of both the SHA and version tags.

The workflow uses the repository `GITHUB_TOKEN` with only the permissions needed
for package publication and GitHub artifact attestations. It does not require
AWS credentials, Docker Hub credentials, provider keys, or Terraform apply.

### Free runtime verification

The local kind workflow remains the authoritative Kubernetes runtime path:

```bash
bash scripts/k8s-local-up.sh
bash scripts/k8s-verify.sh
```

It verifies:

- enforced `restricted` Pod Security;
- namespace-wide default-deny NetworkPolicy;
- two independent gateway replicas;
- shared Redis rate/replay state;
- shared PostgreSQL identity/usage state;
- Prometheus gateway discovery;
- OpenTelemetry trace export;
- replica deletion and replacement;
- bounded concurrent load and resource declarations;
- mock-provider operation without provider spend.

## Optional AWS reference architecture

The production-style reference path contains:

- `terraform/bootstrap`;
- `terraform/aws`;
- `k8s/cloud`;
- `scripts/aws-cloud-preflight.sh`;
- `scripts/aws-cloud-deploy.sh`;
- `scripts/aws-cloud-verify.sh`.

The design includes:

- Amazon EKS for the gateway;
- ECR for immutable images;
- RDS PostgreSQL for client/key and usage state;
- ElastiCache Valkey for rate limiting and execution replay;
- KMS for encryption;
- Secrets Manager for runtime secrets;
- EKS Pod Identity for short-lived workload credentials;
- isolated data subnets and private-by-default services;
- a protected S3 Terraform backend.

Deploying this architecture is not required for the stable release and must not
be described as runtime-verified unless someone deliberately pays for and tests
it.

## Runtime identities

Gateway and migration use separate service accounts and Pod Identity roles.

`sag-gateway` can read only its required runtime secret containers and use the
configured IAM-authenticated Valkey service.

`sag-migration` can read the RDS administrative bootstrap secret and runtime
database credential needed for schema migration and restricted-login setup.

Gateway pods cannot read the RDS master-user secret. The migration role does not
receive provider-secret or ordinary gateway runtime permissions.

## PostgreSQL privilege split

The AWS migration Job runs `python -m app.aws_migrate` with the administrative
credential. It applies versioned migrations and creates or rotates a restricted
`sag_runtime` login with data-plane grants.

The gateway resolves only the restricted runtime credential into `DATABASE_URL`,
requires TLS, and does not own schema migration privileges.

## Valkey IAM authentication

Cloud Redis-compatible connections use `SAG_REDIS_AUTH_MODE=elasticache_iam`
and a `rediss://` URL. The AWS SDK credential chain supplied through Pod
Identity is used to generate short-lived ElastiCache IAM authentication tokens.

The same client factory is used for rate limiting, execution replay, and
Kubernetes readiness checks.

## Network policy

The cloud overlay begins with ingress/egress default deny. Backend policies are
rendered at deployment time from Terraform private/data subnet CIDRs rather than
committed placeholder addresses.

Terraform enables EKS VPC CNI NetworkPolicy support and creates a private
Secrets Manager interface endpoint. The gateway therefore does not need
unrestricted Internet HTTPS egress to load its AWS runtime secrets.

The default cloud provider is `mock`. A live-provider deployment must add a
controlled environment-specific provider egress path.

## Secret handling

No cloud runtime Kubernetes `Secret` object is committed. Deployment-time
configuration contains endpoints, AWS region/resource identifiers, and secret
IDs rather than secret values.

Runtime values are loaded from Secrets Manager into process memory immediately
before the normal application entrypoint. Local ignored files and generated
kind Secrets belong only to the local verification path.

## Optional AWS preflight

A non-mutating real-account inspection can be run deliberately with:

```bash
bash scripts/aws-cloud-preflight.sh
```

It may authenticate to AWS and generate Terraform plans, but it does not create
AWS resources. It is not required by the project roadmap.

## Paid AWS apply guard

Any command capable of creating the optional AWS runtime requires deliberate
opt-in:

```bash
export SAG_ALLOW_BILLABLE_AWS=YES
export SAG_CONFIRM_AWS_APPLY=YES
```

CI never sets either variable.

## Verification language

Use these terms precisely:

- **CI-verified**: exercised by GitHub Actions;
- **locally verified**: exercised on Docker/kind;
- **release-verified**: built, published, and verified by the GHCR workflow;
- **AWS reference-only**: statically validated without a paid runtime claim.

The AWS reference can remain undeployed without blocking `v1.0.0`.
