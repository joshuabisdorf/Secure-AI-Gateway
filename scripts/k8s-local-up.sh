#!/usr/bin/env bash
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

CLUSTER_NAME="secure-ai-gateway"
NAMESPACE="secure-ai-gateway"
IMAGE="secure-ai-gateway:local"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR missing_command=$1" >&2
    exit 2
  fi
}

require_command docker
require_command kind
require_command kubectl
require_command python

if [ ! -f .env ]; then
  echo "ERROR missing_file=.env" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "ERROR missing_env=POSTGRES_PASSWORD" >&2
  exit 2
fi

if ! kind get clusters 2>/dev/null | grep -Fxq "$CLUSTER_NAME"; then
  kind create cluster \
    --name "$CLUSTER_NAME" \
    --config k8s/local/kind-config.yaml \
    --wait 180s
fi

kubectl config use-context "kind-$CLUSTER_NAME" >/dev/null

echo "Building $IMAGE"
docker build --tag "$IMAGE" .
kind load docker-image "$IMAGE" --name "$CLUSTER_NAME"

kubectl apply -f k8s/base/namespace.yaml >/dev/null

DATABASE_URL_K8S="$({ POSTGRES_PASSWORD="$POSTGRES_PASSWORD" python - <<'PY'
import os
from urllib.parse import quote

password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
print(f"postgresql://sag:{password}@sag-postgres:5432/secure_ai_gateway")
PY
} )"

kubectl -n "$NAMESPACE" create secret generic sag-runtime-secrets \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --from-literal=DATABASE_URL="$DATABASE_URL_K8S" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  --from-literal=OPENAI_BASE_URL="${OPENAI_BASE_URL:-}" \
  --from-literal=OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
  --from-literal=OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-}" \
  --from-literal=SAG_TOOL_EXECUTION_SIGNING_KEY="${SAG_TOOL_EXECUTION_SIGNING_KEY:-}" \
  --dry-run=client -o yaml \
  | kubectl apply -f - >/dev/null

kubectl apply -k k8s/local >/dev/null

kubectl -n "$NAMESPACE" rollout status statefulset/sag-postgres --timeout=180s
kubectl -n "$NAMESPACE" rollout status statefulset/sag-redis --timeout=180s
kubectl -n "$NAMESPACE" rollout status deployment/sag-otel-collector --timeout=180s

kubectl -n "$NAMESPACE" delete job sag-migrate --ignore-not-found >/dev/null
kubectl apply -f k8s/base/migration-job.yaml >/dev/null
kubectl -n "$NAMESPACE" wait \
  --for=condition=complete \
  job/sag-migrate \
  --timeout=180s

kubectl -n "$NAMESPACE" rollout status deployment/sag-gateway --timeout=240s
kubectl -n "$NAMESPACE" rollout status deployment/sag-prometheus --timeout=180s

CLIENT_METADATA="$(
  kubectl -n "$NAMESPACE" exec deploy/sag-gateway -- \
    python -m app.clients list
)"

if printf '%s\n' "$CLIENT_METADATA" \
  | grep -q 'client_id=local-dev .*client_active=true .*key_active=true'; then
  CLIENT_OUTPUT="$(
    kubectl -n "$NAMESPACE" exec deploy/sag-gateway -- \
      python -m app.clients rotate local-dev
  )"
else
  CLIENT_OUTPUT="$(
    kubectl -n "$NAMESPACE" exec deploy/sag-gateway -- \
      python -m app.clients create local-dev
  )"
fi

CLIENT_KEY="$(printf '%s\n' "$CLIENT_OUTPUT" | sed -n 's/^API key: //p' | tail -n 1)"
if [ -z "$CLIENT_KEY" ]; then
  echo "ERROR client_bootstrap_failed" >&2
  exit 2
fi

umask 077
printf 'SAG_CLIENT_API_KEY=%s\n' "$CLIENT_KEY" > .k8s-client.env
chmod 600 .k8s-client.env
unset CLIENT_KEY CLIENT_OUTPUT DATABASE_URL_K8S

echo "Kubernetes client credential written to .k8s-client.env"
echo "Gateway replicas:"
kubectl -n "$NAMESPACE" get pods \
  -l app.kubernetes.io/component=gateway \
  -o wide

echo "Services:"
kubectl -n "$NAMESPACE" get services
