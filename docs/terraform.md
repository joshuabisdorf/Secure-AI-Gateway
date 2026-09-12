# Terraform AWS foundation

Secure AI Gateway includes Terraform for an AWS cloud foundation. This milestone defines and validates infrastructure as code but does **not** apply it to an AWS account. Actual cloud deployment, runtime secret population, Kubernetes workload rollout, DNS/TLS, and internet exposure remain separate deployment work.

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

`terraform/bootstrap` owns only the Terraform state bucket and its encryption key. `terraform/aws` owns the application infrastructure. Separating them prevents the state store from being managed by the state it contains.

## State bootstrap

The bootstrap stack creates:

- an S3 bucket with public access blocked;
- bucket-owner-enforced object ownership;
- versioning for state recovery;
- customer-managed KMS encryption with automatic key rotation;
- a bucket policy that denies non-TLS access.

The S3 bucket and KMS key use `prevent_destroy = true`. Removing the state store therefore requires an intentional source change rather than an ordinary `terraform destroy`.

Terraform's native S3 lockfile is used instead of DynamoDB locking. No AWS credentials, backend credentials, or bucket names are committed to the repository.

Example bootstrap flow:

```bash
cd terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars and choose a globally unique state bucket name.
terraform init
terraform plan
terraform apply
terraform output -raw backend_init_example
```

The generated `terraform.tfvars` is ignored by Git.

## AWS environment architecture

The AWS root creates:

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

The data subnets do not receive a default internet route. RDS and ElastiCache are not publicly accessible and accept traffic only from the EKS cluster security group.

## EKS

The EKS cluster uses Kubernetes API authentication mode `API`; it does not depend on the legacy `aws-auth` ConfigMap. Cluster-creator admin permissions are disabled. An optional `eks_admin_role_arn` can be granted cluster-admin access through an EKS access entry and AWS-managed EKS access policy.

The Kubernetes API is private by default. `eks_public_access_cidrs = []` means no public API endpoint. A public endpoint is created only when trusted CIDRs are explicitly configured.

Control-plane API, audit, authenticator, controller-manager, and scheduler logs are enabled. Kubernetes Secrets receive envelope encryption with the environment KMS key. The cluster opts into EKS standard support rather than silently remaining on paid extended support.

Managed worker nodes run in private subnets. The node role receives the EKS worker, ECR pull-only, and VPC CNI policies required for the current managed-node model.

## Workload identity

The Terraform root creates an EKS Pod Identity role for:

```text
namespace:       secure-ai-gateway
service account: sag-gateway
```

The role is intentionally narrow. It can:

- read the three runtime Secret containers;
- decrypt those Secrets through Secrets Manager;
- authenticate to the gateway Valkey replication group as the configured ElastiCache IAM user.

The Pod Identity Agent add-on is provisioned with the cluster. The Kubernetes `sag-gateway` ServiceAccount itself is created during the cloud-deployment milestone, not by Terraform.

## PostgreSQL

RDS PostgreSQL is deployed only into isolated data subnets with storage encryption and forced SSL. Terraform enables IAM database authentication for future runtime hardening.

The administrative RDS password is generated and managed by RDS/Secrets Manager using the environment KMS key; no master password variable is accepted by this Terraform root.

The gateway must not run permanently with the RDS administrative credential. The cloud-deployment stage is responsible for creating/rotating a least-privilege application database identity and populating the dedicated `database-credentials` Secret container.

`protect_data = true` enables RDS deletion protection, retains automated backups, and requires a final snapshot. The development example leaves it false so a disposable environment can be destroyed deliberately.

## Valkey

The ElastiCache replication group uses:

- Valkey rather than an older Redis OSS engine;
- two cache nodes with automatic failover and Multi-AZ enabled;
- isolated data subnets;
- at-rest encryption using the environment KMS key;
- TLS in transit;
- RBAC with an IAM-authenticated gateway user.

IAM authentication avoids storing a long-lived Valkey password. Tokens are short-lived and must be generated/refreshed by the cloud runtime. The existing local Redis configuration remains unchanged; IAM/TLS client integration belongs to the cloud-deployment milestone.

## Runtime Secrets

Terraform creates only Secret **containers** for:

```text
<environment>/provider-credentials
<environment>/database-credentials
<environment>/tool-execution-signing-key
```

It does not create Secret versions or accept provider API keys/tool signing keys as Terraform variables. This prevents those runtime secret values from being deliberately written into Terraform configuration or state during this milestone.

The cloud-deployment stage will populate and consume these secrets using an approved runtime mechanism.

## ECR

The ECR repository uses:

- immutable image tags;
- KMS encryption;
- scan-on-push;
- lifecycle cleanup for old images.

`ecr_force_delete` defaults to false so destroying infrastructure does not silently delete a non-empty image repository.

## NAT topology and cost

`nat_gateway_mode` supports:

```text
single  -> one NAT gateway, lower development cost, one zonal egress dependency
per_az  -> one NAT gateway per Availability Zone, higher availability and cost
```

The development example uses `single`. A persistent/production environment should normally use `per_az`, enable RDS Multi-AZ, and set `protect_data = true` after reviewing expected AWS charges.

## Initializing the AWS root

After the bootstrap stack exists, initialize the application root using the partial S3 backend values printed by `backend_init_example`.

For example:

```bash
cd terraform/aws
cp dev.tfvars.example dev.tfvars

terraform init -reconfigure \
  -backend-config="bucket=YOUR_STATE_BUCKET" \
  -backend-config="region=us-east-1" \
  -backend-config="key=secure-ai-gateway/dev/terraform.tfstate" \
  -backend-config="use_lockfile=true" \
  -backend-config="encrypt=true" \
  -backend-config="kms_key_id=YOUR_STATE_KMS_KEY_ARN"

terraform plan -var-file=dev.tfvars
```

Do not run `terraform apply` until the AWS account, region, cost profile, EKS access path, and runtime secret strategy have been reviewed for the target deployment.

## AWS authentication

Do not put AWS access keys in `.tf`, `.tfvars`, backend arguments, or repository secrets unless a later CI/CD design explicitly requires them.

For interactive work, use AWS IAM Identity Center, an assumable role, or another short-lived credential source supported by the AWS SDK credential chain. `eks_admin_role_arn` should likewise reference a role intended for temporary/federated access rather than a long-lived IAM user.

## Local validation

With Terraform installed:

```bash
terraform fmt -check -recursive terraform

terraform -chdir=terraform/bootstrap init -backend=false -input=false
terraform -chdir=terraform/bootstrap validate

terraform -chdir=terraform/aws init -backend=false -input=false
terraform -chdir=terraform/aws validate
```

These commands download provider schemas but do not create AWS resources and do not require provider API keys used by the gateway.

## CI

GitHub Actions installs Terraform 1.16.2 and runs format, initialization with backends disabled, and validation for both Terraform roots. CI does not run `plan` or `apply` and receives no AWS credentials.

This deliberately keeps pull-request validation side-effect free. A later cloud-deployment workflow can use GitHub OIDC to assume a narrowly scoped deployment role rather than storing long-lived AWS access keys.

## Current boundary

Terraform now defines the cloud foundation, but the following are intentionally **not** claimed by this milestone:

- an AWS account has been modified;
- the gateway image has been pushed to ECR;
- Kubernetes manifests have been adapted/applied to EKS;
- runtime Secret values have been populated;
- the gateway has IAM-authenticated Valkey or cloud database client integration enabled;
- ingress, TLS certificates, DNS, WAF, or a public load balancer exist;
- production alerts, backups, restore exercises, or disaster recovery have been validated.

Those are deployment/hardening concerns and are the next milestones after Terraform validation.
