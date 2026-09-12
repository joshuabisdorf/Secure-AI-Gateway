# Kubernetes

Secure AI Gateway includes a Kustomize-based Kubernetes deployment and a local kind workflow designed to verify multi-replica security behavior without making live provider calls.

## Architecture

The local topology is:

```text
client
  |
  v
sag-gateway Service
  |
  +--> gateway replica A ----+
  |                          |
  +--> gateway replica B ----+--> PostgreSQL
                             +--> Redis
                             +--> OpenTelemetry Collector

Prometheus -- Kubernetes pod discovery --> gateway replica /metrics endpoints
```

The gateway runs with two replicas. PostgreSQL remains authoritative for gateway identities and daily usage totals. Redis remains authoritative for distributed rate limits and execution-ticket replay protection. This means security decisions are not dependent on which gateway replica handles a request.

The local PostgreSQL and Redis workloads are development infrastructure only. A production/cloud deployment should use appropriately operated or managed data services rather than treating these single-replica StatefulSets as highly available databases.

## Manifest layout

```text
k8s/
├── base/
│   ├── config.yaml
│   ├── gateway.yaml
│   ├── kustomization.yaml
│   └── namespace.yaml
├── migration/
│   ├── job.yaml
│   └── kustomization.yaml
├── ci/
│   └── kustomization.yaml
└── local/
    ├── infrastructure.yaml
    ├── kind-config.yaml
    ├── kustomization.yaml
    └── observability.yaml
```

`k8s/base` contains the replicated gateway workload and non-secret security configuration. `k8s/migration` contains the explicit database migration Job. `k8s/local` adds PostgreSQL, Redis, Prometheus, and the OpenTelemetry Collector. `k8s/ci` renders the local stack plus the migration component for schema validation.

## ConfigMap and Secret boundary

Tracked ConfigMaps contain only non-secret configuration:

- provider selection for the deterministic local deployment;
- backend selections and service addresses;
- global model ceiling;
- security policy JSON;
- authoritative tool-execution policy JSON;
- OpenTelemetry service/export configuration.

The gateway references a runtime Secret named:

```text
sag-runtime-secrets
```

The repository does not contain that Secret. `scripts/k8s-local-up.sh` creates it from the ignored `.env` using `kubectl create secret ... --dry-run=client | kubectl apply` without printing secret values.

The local Secret contains the PostgreSQL password and derived database URL, optional provider credentials, and the tool-execution signing key. Kubernetes Secrets are an API/storage mechanism, not encryption by themselves; production secret encryption, external secret management, RBAC, and workload identity belong in the cloud/hardening milestones.

## Gateway pod security

Gateway pods preserve the container restrictions used by the Docker deployment:

- non-root UID/GID 10001;
- read-only root filesystem;
- `allowPrivilegeEscalation: false`;
- all Linux capabilities dropped;
- `RuntimeDefault` seccomp profile;
- service-account token automount disabled;
- memory-backed bounded `/tmp`;
- explicit CPU and memory requests/limits.

The Deployment uses two replicas, rolling updates with zero planned unavailable replicas, a preferred topology spread across nodes, and a PodDisruptionBudget requiring at least one gateway replica to remain available.

## Liveness, startup, and readiness

`/health` remains a lightweight process-health endpoint. Kubernetes uses it for startup and liveness checks.

Readiness is intentionally stricter. The readiness probe executes:

```bash
python -m app.readiness
```

The probe checks only the shared backends required by the current runtime configuration. PostgreSQL readiness requires both connectivity and the migrated gateway tables. Redis readiness requires a successful bounded-timeout ping.

A temporary PostgreSQL or Redis outage therefore removes the affected gateway pod from Service traffic without causing the liveness probe to repeatedly restart a healthy Python process.

The readiness command prints only safe component names such as `postgres` or `redis`; it never prints connection strings, credentials, or backend exception bodies.

## Database migrations

Container startup still runs migrations by default for Docker development.

Kubernetes gateway replicas receive:

```text
SAG_RUN_MIGRATIONS=false
```

so schema ownership is removed from application-pod startup. `k8s/migration` is the explicit migration Kustomize component. The local bootstrap script deletes/recreates that Job, waits for it to complete, and only then waits for the gateway Deployment to become available.

This prevents two gateway replicas from racing to own migration sequencing and provides a deployment point that can later become a pre-deploy cloud/CD step.

## Prometheus

Prometheus uses Kubernetes pod discovery rather than scraping the `sag-gateway` Service. Its namespaced Role grants only `get`, `list`, and `watch` on Pods in `secure-ai-gateway`.

Relabeling keeps only pods with:

```text
app.kubernetes.io/name=secure-ai-gateway
app.kubernetes.io/component=gateway
```

and the named `http` container port. This preserves per-replica visibility instead of hiding replicas behind a load-balanced Service scrape.

The `/metrics` endpoint remains unauthenticated for scraper compatibility and therefore must remain inside the monitoring/network trust boundary in production.

## OpenTelemetry

Gateway replicas export OTLP/HTTP spans to:

```text
http://sag-otel-collector:4318/v1/traces
```

The local collector uses the debug exporter only. This verifies that traces from multiple gateway replicas cross the OTLP boundary; it is not a persistent production trace backend.

## Local kind deployment

Prerequisites:

- Docker;
- `kubectl`;
- kind;
- Python;
- an ignored `.env` with at least `POSTGRES_PASSWORD` configured.

The local Kubernetes configuration deliberately uses the mock provider, so provider API keys are not required and verification does not make a live LLM call.

Bootstrap or refresh the cluster with:

```bash
bash scripts/k8s-local-up.sh
```

The script:

1. creates or reuses a three-node kind cluster named `secure-ai-gateway`;
2. builds `secure-ai-gateway:local`;
3. loads the image into the kind nodes;
4. creates the runtime Kubernetes Secret from `.env`;
5. applies the base/local Kustomize manifests;
6. waits for PostgreSQL, Redis, and the collector;
7. runs the dedicated migration Job from `k8s/migration`;
8. waits for both gateway replicas and Prometheus;
9. creates or rotates the `local-dev` gateway API key inside PostgreSQL;
10. writes the raw client credential to ignored `.k8s-client.env` with mode `0600`.

Do not commit or paste `.k8s-client.env`. It is also excluded from the Docker build context.

## Multi-replica verification

Run:

```bash
bash scripts/k8s-verify.sh
```

The verification script forwards directly to two different gateway pods and sends one authenticated request to each. It verifies:

- both pods return HTTP 200;
- the second pod observes a lower Redis-backed rate-limit remainder than the first;
- the second pod observes a higher PostgreSQL-backed cumulative usage count than the first;
- both responses contain 32-character trace IDs;
- Prometheus sees healthy gateway pod targets;
- the OpenTelemetry Collector receives traces.

Because the two requests are pinned to different pods, the Redis/PostgreSQL checks specifically exercise shared state across replicas rather than accidentally testing the same process twice.

## Manual access

Gateway Service:

```bash
kubectl -n secure-ai-gateway port-forward service/sag-gateway 18000:8000
```

Then from another shell:

```bash
set -a
source .k8s-client.env
set +a
curl -i \
  -X POST http://127.0.0.1:18000/v1/chat/completions \
  -H "Authorization: Bearer $SAG_CLIENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openrouter/free","messages":[{"role":"user","content":"Kubernetes works"}]}'
```

Prometheus:

```bash
kubectl -n secure-ai-gateway port-forward service/sag-prometheus 19090:9090
```

The local UI is then available at `http://127.0.0.1:19090`.

## Persistent local state

PostgreSQL, Redis, and Prometheus request PersistentVolumeClaims from the cluster's default StorageClass. They persist across ordinary Pod/container restarts while the kind cluster and its volumes exist.

Deleting the kind cluster is destructive to this local Kubernetes environment:

```bash
kind delete cluster --name secure-ai-gateway
```

Do not use that command if the cluster-local test data must be preserved.

## CI validation

GitHub Actions renders:

```bash
kubectl kustomize k8s/ci
```

with Kubernetes 1.37 tooling and validates the rendered resources with kubeconform in strict mode. The CI job also rejects rendered tracked `Secret` objects so secret material remains a runtime concern.

This is manifest/schema validation, not a live Kubernetes integration cluster. The local kind verification script exercises actual cross-replica runtime behavior.

## Current limitations

This milestone intentionally does not yet provide:

- external Ingress or TLS termination;
- NetworkPolicy enforcement;
- HorizontalPodAutoscaler behavior;
- managed PostgreSQL/Redis;
- external secret management or workload identity;
- a persistent trace backend;
- production image registry/release automation;
- cloud load balancing or DNS.

Those boundaries are intentionally left for Terraform, cloud deployment, and production-hardening milestones rather than being represented as already solved.
