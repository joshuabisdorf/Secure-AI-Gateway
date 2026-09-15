# Kubernetes security model

Secure AI Gateway uses a shared hardened workload base plus environment-specific
network controls. The base does not encode backend addresses because the local
and optional AWS deployments have different network identities.

## Pod Security

The `secure-ai-gateway` namespace enforces the Kubernetes `restricted` Pod
Security profile and also keeps the `restricted` audit and warning labels
enabled. The gateway and migration workloads run non-root with `RuntimeDefault`
seccomp, no privilege escalation, dropped Linux capabilities, and bounded
writable storage. Gateway roots remain read-only.

The local PostgreSQL and Redis StatefulSets use their image-defined non-root
identities explicitly, set an appropriate `fsGroup` for persistent storage,
disable service-account token automount, use `RuntimeDefault` seccomp, disable
privilege escalation, and drop all capabilities.

CI exercises admission by deploying the complete local stack into kind under the
enforcing namespace. A manifest that violates the namespace policy therefore
fails the live kind job rather than only producing a lint warning.

## Default-deny network strategy

Both local and cloud overlays install a namespace-wide ingress/egress default
deny. Allow policies are additive and select only the workloads that need the
path.

The gateway ingress policy exposes only TCP 8000. No NetworkPolicy exposes
PostgreSQL, Redis, OpenTelemetry, or Prometheus broadly.

### Local overlay

The local allow graph is:

```text
client/node -> gateway:8000
gateway -> PostgreSQL:5432
gateway -> Redis:6379
gateway -> OTel Collector:4318
migration -> PostgreSQL:5432
Prometheus -> gateway:8000
Prometheus -> Kubernetes API:443/6443
all selected pods -> CoreDNS:53 UDP/TCP
```

PostgreSQL, Redis, and the OTel Collector have explicit empty egress policies.
Prometheus is allowed only the scrape path, Kubernetes API discovery path, and
the shared DNS policy.

`make kind-verify` creates an otherwise untrusted probe pod and attempts to
connect directly to Redis. The probe must fail under the default-deny policy
while normal gateway traffic continues to use the explicitly allowed Redis path.

### Cloud overlay

Cloud NetworkPolicies are split into two parts.
`k8s/cloud/network-policies.yaml` contains identities known from Kubernetes
labels: default deny, DNS, gateway ingress, gateway-to-collector telemetry,
collector ingress, and Prometheus-to-gateway scraping.

Backend/AWS destinations are rendered at deployment time by
`scripts/render_cloud_network_policies.py`. The renderer receives
`private_subnet_cidrs` and `data_subnet_cidrs` directly from Terraform output.
It does not contain placeholder production CIDRs.

The generated policies allow:

```text
gateway -> data subnet CIDRs:5432/6379
gateway -> private subnet CIDRs:443
gateway -> 169.254.170.23/32:80
migration -> data subnet CIDRs:5432
migration -> private subnet CIDRs:443
migration -> 169.254.170.23/32:80
Prometheus -> private subnet CIDRs:443
```

RDS PostgreSQL and ElastiCache Valkey live in the isolated data subnets.
Terraform creates a private Secrets Manager interface endpoint in the EKS
private subnets, so the gateway does not need unrestricted Internet HTTPS egress
to retrieve runtime secrets. `169.254.170.23` is the IPv4 link-local EKS Pod
Identity Agent credential endpoint used by the AWS SDK in associated pods.

Terraform also manages the EKS `vpc-cni` add-on with Kubernetes NetworkPolicy
support enabled. The AWS reference path remains statically validated rather than
deployed by CI because creating it would incur cloud cost.

The default cloud provider is `mock`; the committed policy intentionally does
not allow arbitrary Internet/provider egress. A deployment that enables a live
provider must add an environment-specific allow policy based on concrete egress
identities supported by that environment, such as controlled NAT/proxy CIDRs or
a CNI with approved FQDN policy. The repository does not claim portable FQDN
filtering through standard Kubernetes NetworkPolicy.

## Runtime identity and secrets

Local and cloud secret handling differ deliberately.

In local kind, `scripts/k8s-local-up.sh` creates `sag-runtime-secrets` from the
ignored `.env` file. The Secret is runtime-generated and is never committed.
Gateway and migration consume it only where required. Kubernetes Secret storage
should not be interpreted as application-level encryption.

In the AWS overlay, gateway and migration use separate service accounts,
`sag-gateway` and `sag-migration`, with token automount disabled. EKS Pod
Identity associations bind those service accounts to separate IAM roles. The EKS
Pod Identity Agent injects its own short-lived credential token mount and
link-local credential endpoint. Runtime database credentials and the
tool-signing key are fetched from AWS Secrets Manager; they are not rendered
into Kubernetes Secret manifests.

Prometheus is the exception to token automount in the monitoring path. Its
service account receives a namespaced Role limited to `get`, `list`, and `watch`
Pods so Kubernetes service discovery can enumerate gateway targets. Gateway
application pods do not receive Kubernetes API credentials.

## Resilience and resource verification

The gateway Deployment remains at two replicas with a PodDisruptionBudget,
readiness/liveness/startup probes, rolling updates with zero planned
unavailability, and explicit CPU/memory requests and limits.

The live kind verification now:

- checks `restricted` Pod Security enforcement and the default-deny policy;
- proves a non-allowed Redis connection is denied;
- sends authenticated requests to two separate gateway replicas and verifies
  shared Redis/PostgreSQL state;
- deletes one gateway pod while sending through the surviving replica;
- waits for a distinct replacement pod and verifies it can serve authenticated
  traffic;
- runs a bounded concurrent request burst across the surviving/replacement
  replicas;
- fails on unexpected HTTP status, gateway restart, or observed `OOMKilled`
  state;
- verifies the configured resource requests and limits are present.

This is a deterministic resilience/security test, not a capacity benchmark.
Performance baseline methodology remains in `docs/performance.md`.

## Boundaries

Standard Kubernetes NetworkPolicy controls IP/port traffic; it does not
authenticate application protocols, constrain DNS names, or replace security
groups, IAM, TLS, provider authorization, or gateway policy. The optional AWS
architecture therefore combines NetworkPolicy with isolated data subnets,
security groups, private Secrets Manager access, EKS Pod Identity, and the
application-level gateway controls.
