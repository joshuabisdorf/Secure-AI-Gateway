# ADR 0002: Keep the required project path zero-cost

- Status: Accepted
- Date: 2026-09-12

## Context

A production-style gateway benefits from showing how it would integrate with managed cloud networking, Kubernetes, database, cache, secret-management, identity, and container-registry services. Applying that infrastructure can create recurring charges even when the application itself is a portfolio project.

The project requirement is that normal development, verification, and delivery cost the owner nothing.

## Decision

The required/verified path is zero-cost and uses:

- local Python tooling;
- deterministic mock-provider calls;
- Docker Compose;
- kind/Kubernetes on the developer machine;
- GitHub Actions for a public repository;
- public GitHub Container Registry packaging;
- Terraform static/provider-schema validation without apply.

The AWS Terraform/EKS/RDS/Valkey/ECR design remains an optional production-style reference implementation. Applying billable AWS resources is not a prerequisite for project completion or `v1.0.0`.

Repository AWS deployment helpers fail closed unless both explicit billable-resource and apply-confirmation environment flags are deliberately set.

## Consequences

Benefits:

- reviewers can reproduce the security story without a billing account;
- CI and demos cannot accidentally create the AWS stack;
- cloud architecture skill remains visible through Terraform and cloud manifests;
- paid components may remain runtime-untested without blocking the portfolio release.

Costs:

- AWS runtime behavior is not claimed as verified unless someone deliberately deploys it;
- cloud-specific ingress, DNS/TLS, IAM, managed-service versions, and cost characteristics may drift and require review before a real deployment;
- the production reference architecture is intentionally stronger evidence of design skill than of live cloud operations.

## Rejected alternatives

### Require a live AWS deployment for release

Rejected because EKS and supporting managed services can create recurring charges and violate the top-level cost requirement.

### Remove cloud infrastructure entirely

Rejected because Terraform, EKS Pod Identity, managed PostgreSQL/Valkey, secret boundaries, and private-network design are useful architectural artifacts even when unapplied.

### Use static long-lived AWS keys in CI

Rejected. If optional live cloud deployment is ever automated, short-lived federated identity such as GitHub OIDC should be used instead.
