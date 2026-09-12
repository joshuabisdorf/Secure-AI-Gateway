# Terraform AWS reference architecture

Secure AI Gateway includes Terraform for a production-style AWS target, but the project itself is maintained under a zero-cost-by-default constraint.

Terraform therefore serves as a **reference architecture plus static validation target**. Applying it to AWS is optional and is not required for normal development, CI, release, portfolio completion, or runtime verification.

See `docs/cost-policy.md`.

## Toolchain

The configuration targets:

- Terraform `>= 1.16.2, < 1.17.0`;
- HashiCorp AWS provider `~> 6.62.0`;
- Amazon EKS Kubernetes `1.36` by default;
- Amazon RDS for PostgreSQL `18.6` by default;
- Amazon ElastiCache for Valkey `8.2` by default.

Versions are constrained so CI and developer validation do not silently move across a Terraform or provider minor release.

## Layout

```text
terraform/
├── bootstrap/
│   ├── main.tf
│   ├── outputs.tf
│   ├── terraform.tfvars.example
│   ├── variables.tf
│   └── versions.tf
└── aws/
    ├── data-services.tf
    ├── dev.tfvars.example
    ├── ecr.tf
    ├── eks.tf
    ├── locals.tf
    ├── network.tf
    ├── outputs.tf
    ├── security.tf
    ├── variables.tf
    └── versions.tf
```

`terraform/bootstrap` owns only the Terraform state bucket and its encryption key. `terraform/aws` owns the optional application infrastructure. Separating them prevents the state store from being managed by the state it contains.

## State bootstrap design

The bootstrap stack defines:

- an S3 bucket with public access blocked;
- bucket-owner-enforced object ownership;
- versioning for state recovery;
- customer-managed KMS encryption with key rotation;
- a bucket policy denying non-TLS access.

The S3 bucket and KMS key use `prevent_destroy = true`.

Terraform's native S3 lockfile is used instead of DynamoDB locking. No AWS credentials, backend credentials, or bucket names are committed to the repository.

This design is statically validated in CI. Creating the bucket/key is optional and leaves the zero-cost project path.

## AWS environment architecture

The AWS root models:

```text
Internet
   |
Internet Gateway
   |
public subnets (load-balancer reservation)
   |
NAT gateway(s)
   |
private subnets
   +--> EKS managed nodes
   |
   +--------------------------+
                              |
isolated data subnets         |
   +--> RDS PostgreSQL <-------+
   +--> ElastiCache Valkey <---+

EKS --> ECR
EKS workload role --> Secrets Manager
EKS workload role --> Valkey IAM authentication
```

The data subnets do not receive a default internet route. RDS and ElastiCache are private and accept traffic only from the EKS cluster security group.

## EKS

The EKS cluster uses Kubernetes API authentication mode `API`; it does not depend on the legacy `aws-auth` ConfigMap. Cluster-creator admin permissions are disabled. An optional `eks_admin_role_arn` can be granted cluster-admin access through an EKS access entry and AWS-managed EKS access policy.

The Kubernetes API is private by default. `eks_public_access_cidrs = []` means no public endpoint.

Control-plane API, audit, authenticator, controller-manager, and scheduler logs are enabled. Kubernetes Secrets receive envelope encryption with the environment KMS key. The cluster opts into standard support.

Managed worker nodes run in private subnets.

## Workload identity

The Terraform root defines EKS Pod Identity roles for separate runtime and migration responsibilities.

The gateway role can read only its runtime Secrets Manager containers and authenticate to the configured IAM-enabled Valkey service.

The migration role can read the RDS administrative bootstrap credential and runtime database credential needed to apply migrations and provision the restricted application login.

The Kubernetes ServiceAccounts and Pod Identity associations are represented by the optional cloud deployment target in `k8s/cloud`.

## PostgreSQL

RDS PostgreSQL is modeled only in isolated data subnets with storage encryption and forced SSL.

The administrative password is generated and managed by RDS/Secrets Manager. No master password variable is accepted by the Terraform root.

The optional cloud migration stage provisions a separate restricted `sag_runtime` login; gateway pods are not intended to use the RDS administrative identity.

`protect_data = true` enables deletion protection, retained automated backups, and a final snapshot. The development example leaves it false only for a deliberately disposable reference environment.

## Valkey

The ElastiCache design uses:

- Valkey;
- two cache nodes with automatic failover and Multi-AZ;
- isolated data subnets;
- at-rest KMS encryption;
- TLS in transit;
- RBAC with an IAM-authenticated gateway user.

The application-side IAM/TLS integration is implemented and covered by offline tests with injected fake AWS credentials; a live ElastiCache environment is not required.

## Runtime secrets

Terraform creates only Secret **containers** for:

```text
<environment>/provider-credentials
<environment>/database-credentials
<environment>/tool-execution-signing-key
```

It does not accept provider API keys or the tool signing key as Terraform variables.

The optional AWS runtime loader resolves those values in memory. Routine free verification uses the mock provider and local ignored configuration instead.

## ECR

The optional ECR repository design uses immutable tags, KMS encryption, scan-on-push, and lifecycle cleanup.

The maintained free release path does **not** require ECR. Public release images are published with `.github/workflows/release.yml` to GitHub Container Registry.

## NAT topology and cost boundary

`nat_gateway_mode` supports:

```text
single  -> one NAT gateway, lower reference-environment cost
per_az  -> one NAT gateway per Availability Zone, higher availability and cost
```

This is a design tradeoff only. Neither option is required to be deployed for the project.

The optional AWS architecture contains services that can incur charges even with very little application traffic. For that reason it is not part of the default verified path.

## Free local validation

With Terraform installed:

```bash
terraform fmt -check -recursive terraform
terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate
terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate
```

These commands download provider schemas but do not create AWS resources.

CI performs the same validation with no AWS credentials.

## Optional AWS inspection

Someone who deliberately wants to inspect the reference architecture against a real AWS account may run the non-mutating preflight:

```bash
bash scripts/aws-cloud-preflight.sh
```

AWS signup/authentication is not a project prerequisite.

## Paid apply guard

Any Terraform apply operation requires both explicit environment acknowledgements:

```bash
SAG_ALLOW_BILLABLE_AWS=YES
SAG_CONFIRM_AWS_APPLY=YES
```

The deployment phase also requires `SAG_ALLOW_BILLABLE_AWS=YES` because it assumes and uses an already-running paid AWS environment.

CI never sets either variable.

## Verification claims

The repository may claim that the Terraform architecture is:

- formatted;
- provider-schema validated;
- represented by schema-validated Kubernetes cloud manifests;
- covered by offline application tests for AWS secret loading and Valkey IAM token construction.

Unless someone deliberately deploys and verifies the AWS environment, the repository must **not** claim that EKS/RDS/ElastiCache runtime behavior has been live-tested.

That reference-only status does not block the roadmap. The required next milestone after the zero-cost release path is production/adversarial hardening.
