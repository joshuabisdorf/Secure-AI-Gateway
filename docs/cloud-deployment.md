# AWS cloud deployment

The AWS deployment path connects the validated Terraform foundation to the Kubernetes gateway without committing credentials or making the gateway public by default.

This workflow is intentionally staged. Repository CI never runs `terraform plan`, `terraform apply`, `aws eks update-kubeconfig`, ECR pushes, or Kubernetes deployment against an AWS account.

## Architecture

The development cloud target uses:

- Amazon EKS for the two-replica gateway deployment;
- Amazon ECR for immutable `sha-*` gateway images;
- Amazon RDS for PostgreSQL client/key and daily-usage state;
- Amazon ElastiCache for Valkey distributed rate-limit and execution-ticket replay state;
- AWS KMS for Terraform state and application data-service encryption;
- AWS Secrets Manager for runtime database credentials, provider credentials, and the execution-ticket signing key;
- EKS Pod Identity for short-lived AWS credentials inside gateway and migration pods;
- cluster-internal Prometheus and OpenTelemetry Collector deployments;
- an S3 backend using Terraform native S3 lockfiles.

No public Kubernetes `LoadBalancer` or Ingress is created. The gateway Service, Prometheus, and OTLP receiver remain `ClusterIP` resources. Development verification uses `kubectl port-forward`.

## Runtime identities

Two service accounts have separate Pod Identity roles.

`sag-gateway` can:

- read the runtime database secret;
- read the execution-ticket signing-key secret;
- read the optional provider-credential secret;
- decrypt those secrets through the platform KMS key;
- call `elasticache:Connect` for the configured Valkey replication group and IAM user.

`sag-migration` can:

- read the RDS-managed master-user secret;
- read the runtime database secret;
- decrypt those database bootstrap secrets.

The migration role cannot read provider credentials or the execution-ticket signing key and does not receive Valkey connect permission. Gateway pods cannot read the RDS master-user secret.

## PostgreSQL privilege split

The migration Job runs `python -m app.aws_migrate` with the RDS administrative credential. It applies the versioned migrations, then creates or rotates a `sag_runtime` PostgreSQL login with only runtime data-plane grants:

- database `CONNECT`;
- schema `USAGE`;
- `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on tables;
- sequence `USAGE`, `SELECT`, and `UPDATE`;
- matching default privileges for future migration-owned tables and sequences.

The gateway Deployment resolves only the `sag_runtime` credential into `DATABASE_URL` and requires TLS (`sslmode=require`). It does not run schema migrations.

## Valkey IAM authentication

Cloud Redis-compatible connections use `SAG_REDIS_AUTH_MODE=elasticache_iam` and a `rediss://` URL. The gateway uses the AWS SDK default credential chain supplied by EKS Pod Identity to generate SigV4 ElastiCache connect tokens. Tokens are cached for less than their maximum lifetime and regenerated for new connections.

The same client factory is used by:

- distributed rate limiting;
- execution-ticket replay protection;
- Kubernetes readiness checks.

The distributed rate limiter uses a one-key Lua transaction based on `GET`, `SET`, `TTL`, and `INCR`, avoiding Redis-version-specific commands that are not portable to Valkey.

## Secret handling

No Kubernetes `Secret` object is committed in the cloud overlay. Deployment-time configuration contains only non-secret endpoints, AWS region, resource names, and Secrets Manager IDs.

Gateway secret values are loaded in memory by `python -m app.aws_runtime` immediately before it executes the normal gateway entrypoint. Secret values are not written into Kubernetes manifests or printed by the loader.

The deployment script creates an initial runtime database password and execution-ticket signing key only when those Terraform-created Secrets Manager containers do not already have a value. Temporary JSON files are mode `0600` and removed on script exit.

Provider credentials are not populated by the initial cloud deployment. The cloud verification path uses the deterministic mock provider and therefore makes no upstream LLM request.

## EKS API access

Terraform keeps the EKS API private-only when `eks_public_access_cidrs` is empty. This is the secure default.

A workstation outside the VPC cannot use `kubectl` against a private-only endpoint. For a short-lived development environment, an alternative is to explicitly add the workstation's current public IPv4 address as a single `/32` entry in ignored `terraform/aws/dev.tfvars`. A persistent environment should use a private network path such as VPN or a controlled administrative host instead of broadly opening the API.

An EKS administrator role must also be configured with `eks_admin_role_arn`; cluster creator administrator permissions are intentionally disabled.

## Preflight

Prerequisites:

- AWS CLI authenticated with a role that can provision the Terraform resources;
- Terraform 1.16.2;
- Docker;
- kubectl;
- jq;
- curl;
- OpenSSL.

Run:

```bash
bash scripts/aws-cloud-preflight.sh
```

The preflight is non-mutating with respect to AWS resources. It:

- verifies the active AWS identity;
- creates ignored local Terraform variable files when missing;
- proposes an EKS administrator role ARN when the caller is role-based;
- reports the workstation public `/32` when available;
- checks Terraform formatting and validation;
- renders the cloud Kustomize overlay and rejects tracked Kubernetes Secrets;
- generates a Terraform bootstrap plan.

It finishes with `aws_resources_created=0`.

Review the reported identity, region, bootstrap plan, EKS administrator role, and API access path before any apply.

## Apply phases

The apply commands require an explicit environment acknowledgement:

```bash
export SAG_CONFIRM_AWS_APPLY=YES
```

That variable is only a local safety guard; it is not a credential.

### 1. Remote-state foundation

Review again:

```bash
bash scripts/aws-cloud-deploy.sh bootstrap-plan
```

Then explicitly create the S3/KMS state foundation:

```bash
SAG_CONFIRM_AWS_APPLY=YES bash scripts/aws-cloud-deploy.sh bootstrap-apply
```

### 2. Application infrastructure

After the state foundation exists:

```bash
bash scripts/aws-cloud-deploy.sh plan
```

This initializes `terraform/aws` against the encrypted S3 backend and prints the proposed AWS changes. Review this plan before continuing because EKS, EC2/NAT, RDS, and ElastiCache create billable resources.

Apply only after that review:

```bash
SAG_CONFIRM_AWS_APPLY=YES bash scripts/aws-cloud-deploy.sh infra-apply
```

### 3. Application deployment

Once Terraform finishes successfully:

```bash
bash scripts/aws-cloud-deploy.sh deploy
```

The deployment phase:

1. reads only non-secret Terraform outputs and secret ARNs;
2. configures the local kubeconfig for the EKS cluster;
3. initializes missing Secrets Manager runtime values;
4. builds an immutable image tagged from the Git commit SHA;
5. logs into ECR using `aws ecr get-login-password` and pushes the image;
6. creates deployment-time policy/runtime ConfigMaps;
7. renders the cloud overlay with the immutable ECR image reference;
8. runs the dedicated migration Job;
9. waits for gateway, collector, and Prometheus rollouts;
10. creates or rotates the `local-dev` gateway client and stores its raw credential only in ignored `.aws-client.env`.

The deploy phase does not create a public gateway endpoint.

## Verification

Run:

```bash
bash scripts/aws-cloud-verify.sh
```

The verifier sends authenticated requests directly to two distinct gateway pods. It validates:

- HTTP 200 responses;
- 32-character trace IDs;
- cross-replica shared Valkey rate-limit state;
- cross-replica shared PostgreSQL usage state;
- two healthy Prometheus gateway targets;
- OpenTelemetry export to the cluster-local collector;
- two ready gateway replicas.

The expected final line includes:

```text
gateway_replicas=2 shared_valkey=true shared_rds=true prometheus=true otel=true public_endpoint=false
```

## Cost and teardown boundary

`preflight`, `bootstrap-plan`, and application `plan` do not create AWS resources. `bootstrap-apply` creates the state bucket/KMS key. `infra-apply` creates the main billable environment.

Do not run an unreviewed destroy command. The state foundation is intentionally protected from Terraform destroy, and persistent-environment data-protection settings may also prevent destructive operations. Teardown should be handled as an explicit reviewed operation after cloud verification, not bundled into the deployment script.
