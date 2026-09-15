# Kubernetes

Secure AI Gateway includes Kustomize targets for a local kind environment, CI
rendering, an explicit migration Job, and an optional AWS/EKS reference
deployment. The local path verifies the multi-replica security model without
live provider calls or paid cloud infrastructure.

## Layout

```text
k8s/
├── base/       namespace, gateway, shared non-secret configuration
├── local/      PostgreSQL, Redis, observability, local NetworkPolicies
├── migration/  explicit database migration Job
├── ci/         local + migration rendering target
└── cloud/      AWS workload identities, observability, cloud NetworkPolicies
```

The gateway runs with two replicas. PostgreSQL is authoritative for client/key
state and usage accounting; Redis is authoritative for distributed rate limits
and execution-ticket replay claims. Security state therefore remains shared when
traffic moves between replicas.

## Security baseline

The namespace enforces the Kubernetes `restricted` Pod Security profile. Gateway
and migration containers run non-root with `RuntimeDefault` seccomp, no
privilege escalation, dropped capabilities, bounded resources, and no ordinary
service-account token automount. Gateway roots are read-only and use a bounded
memory-backed `/tmp`.

Both local and cloud overlays install namespace-wide ingress/egress default-deny
NetworkPolicies and then add only the required workload paths. The exact allow
graphs, AWS CIDR rendering, runtime identities, and secret boundaries are
documented in [`kubernetes-security.md`](kubernetes-security.md).

## Configuration and secrets

Tracked ConfigMaps contain only non-secret runtime and policy configuration.
Local kind creates the `sag-runtime-secrets` Secret at runtime from ignored
`.env` values; the Secret is not committed.

The AWS overlay does not render gateway database/signing credentials into
Kubernetes Secrets. Separate EKS Pod Identity roles for gateway and migration
fetch runtime material from Secrets Manager. Prometheus is the only workload
that intentionally receives a Kubernetes service-account token, and its
namespaced Role is limited to `get`, `list`, and `watch` Pods for target
discovery.

## Database migrations

Kubernetes gateway pods use `SAG_RUN_MIGRATIONS=false`. Schema changes are owned
by the separate `sag-migrate` Job. Local bootstrap runs the Job before waiting
for the gateway Deployment. The cloud deployment does the same after rendering
the environment-specific runtime configuration and network policies.

This removes migration ownership from application startup and avoids replica
races during rollout.

## Local kind deployment

Prerequisites are Docker, `kubectl`, kind, Python, and an ignored `.env`
containing at least `POSTGRES_PASSWORD`.

```bash
make kind-up
make kind-verify
```

`kind-up` creates or reuses the three-node cluster, builds and loads the gateway
image, creates the runtime Secret, applies the hardened local overlay, runs the
migration Job, waits for shared services, and creates/rotates the `local-dev`
API key. The raw client credential is written only to ignored `.k8s-client.env`
with owner-only permissions.

`kind-verify` performs live verification rather than only schema checks. It
validates Pod Security enforcement and NetworkPolicy denial, sends authenticated
traffic to two separate gateway pods, proves shared Redis/PostgreSQL state,
checks tracing and Prometheus discovery, deletes one gateway pod while
exercising the survivor, waits for a distinct replacement replica, and runs a
bounded concurrent request burst while checking resource bounds, restart counts,
and OOM state.

The local PostgreSQL and Redis StatefulSets remain development infrastructure.
They are not represented as highly available production databases.

## Manual access

Gateway Service:

```bash
kubectl -n secure-ai-gateway port-forward service/sag-gateway 18000:8000
```

Prometheus:

```bash
kubectl -n secure-ai-gateway port-forward service/sag-prometheus 19090:9090
```

Deleting the kind cluster is destructive to its local PVC-backed test data:

```bash
kind delete cluster --name secure-ai-gateway
```

## CI validation

CI performs two separate Kubernetes checks. The manifest job renders local/CI
and cloud Kustomize targets, renders a temporary cloud CIDR policy fixture
through the same production renderer, rejects tracked Secret objects, and
validates all resources strictly against Kubernetes 1.37 schemas.

The `kind security and resilience` job installs a SHA-pinned kind action,
creates an ephemeral mock-provider configuration, deploys the full local stack,
and runs `make kind-verify`. This makes Pod Security, NetworkPolicy, replica
rescheduling, and bounded-load behavior release gates rather than
documentation-only controls.

## Optional AWS path

The Terraform architecture provides EKS, isolated RDS/ElastiCache data subnets,
EKS Pod Identity, KMS/Secrets Manager, a private Secrets Manager interface
endpoint, and the VPC CNI NetworkPolicy feature. `scripts/aws-cloud-deploy.sh`
reads real private/data subnet CIDRs from Terraform output and renders cloud
runtime egress policies before applying workloads.

AWS deployment remains optional and billable. CI validates Terraform and
Kubernetes configuration but does not create cloud resources. See
[`cloud-deployment.md`](cloud-deployment.md) and
[`cost-policy.md`](cost-policy.md).
