#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-plan}"
NAMESPACE="secure-ai-gateway"
BOOTSTRAP_VARS="terraform/bootstrap/terraform.tfvars"
AWS_VARS="terraform/aws/dev.tfvars"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR missing_command=$1" >&2
    exit 2
  fi
}

for command in aws terraform docker kubectl jq openssl git sed; do
  require_command "$command"
done

if [ ! -f "$BOOTSTRAP_VARS" ]; then
  echo "ERROR missing_file=$BOOTSTRAP_VARS run=scripts/aws-cloud-preflight.sh" >&2
  exit 2
fi
if [ ! -f "$AWS_VARS" ]; then
  echo "ERROR missing_file=$AWS_VARS run=scripts/aws-cloud-preflight.sh" >&2
  exit 2
fi

require_apply_confirmation() {
  if [ "${SAG_CONFIRM_AWS_APPLY:-}" != "YES" ]; then
    echo "ERROR explicit_confirmation_required=SAG_CONFIRM_AWS_APPLY=YES" >&2
    exit 2
  fi
}

bootstrap_outputs() {
  STATE_BUCKET="$(terraform -chdir=terraform/bootstrap output -raw state_bucket_name 2>/dev/null || true)"
  STATE_KMS_KEY="$(terraform -chdir=terraform/bootstrap output -raw state_kms_key_arn 2>/dev/null || true)"
  if [ -z "$STATE_BUCKET" ] || [ -z "$STATE_KMS_KEY" ]; then
    echo "ERROR terraform_bootstrap_not_applied=true" >&2
    exit 2
  fi
  REGION="$(awk -F'"' '/^[[:space:]]*aws_region[[:space:]]*=/{print $2; exit}' "$BOOTSTRAP_VARS")"
  if [ -z "$REGION" ]; then
    echo "ERROR bootstrap_region_unavailable=true" >&2
    exit 2
  fi
}

init_application_backend() {
  bootstrap_outputs
  terraform -chdir=terraform/aws init \
    -reconfigure \
    -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="region=$REGION" \
    -backend-config="key=secure-ai-gateway/dev/terraform.tfstate" \
    -backend-config="use_lockfile=true" \
    -backend-config="encrypt=true" \
    -backend-config="kms_key_id=$STATE_KMS_KEY"
}

put_json_secret_if_empty() {
  local secret_arn="$1"
  local payload_file="$2"
  if aws secretsmanager get-secret-value \
      --secret-id "$secret_arn" \
      --query SecretString \
      --output text >/dev/null 2>&1; then
    return 0
  fi
  aws secretsmanager put-secret-value \
    --secret-id "$secret_arn" \
    --secret-string "file://$payload_file" >/dev/null
}

case "$MODE" in
  bootstrap-plan)
    terraform -chdir=terraform/bootstrap init -backend=false -input=false >/dev/null
    terraform -chdir=terraform/bootstrap plan \
      -input=false \
      -var-file=terraform.tfvars
    ;;

  bootstrap-apply)
    require_apply_confirmation
    terraform -chdir=terraform/bootstrap init -backend=false -input=false >/dev/null
    terraform -chdir=terraform/bootstrap apply \
      -input=false \
      -var-file=terraform.tfvars
    ;;

  plan)
    init_application_backend
    terraform -chdir=terraform/aws plan \
      -input=false \
      -var-file=dev.tfvars
    ;;

  infra-apply)
    require_apply_confirmation
    init_application_backend
    terraform -chdir=terraform/aws apply \
      -input=false \
      -var-file=dev.tfvars
    ;;

  deploy)
    init_application_backend

    REGION="$(terraform -chdir=terraform/aws output -raw aws_region)"
    CLUSTER_NAME="$(terraform -chdir=terraform/aws output -raw eks_cluster_name)"
    ECR_REPOSITORY="$(terraform -chdir=terraform/aws output -raw ecr_repository_url)"
    DATABASE_HOST="$(terraform -chdir=terraform/aws output -raw postgres_endpoint)"
    DATABASE_PORT="$(terraform -chdir=terraform/aws output -raw postgres_port)"
    DATABASE_NAME="$(terraform -chdir=terraform/aws output -raw postgres_database_name)"
    RDS_ADMIN_SECRET="$(terraform -chdir=terraform/aws output -raw postgres_master_secret_arn)"
    VALKEY_HOST="$(terraform -chdir=terraform/aws output -raw valkey_primary_endpoint)"
    VALKEY_PORT="$(terraform -chdir=terraform/aws output -raw valkey_port)"
    VALKEY_CACHE_NAME="$(terraform -chdir=terraform/aws output -raw valkey_replication_group_id)"
    VALKEY_USER_ID="$(terraform -chdir=terraform/aws output -raw valkey_iam_user_id)"
    DATABASE_SECRET="$(terraform -chdir=terraform/aws output -json runtime_secret_arns | jq -r '.database_credentials')"
    SIGNING_SECRET="$(terraform -chdir=terraform/aws output -json runtime_secret_arns | jq -r '.tool_signing_key')"

    aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER_NAME" >/dev/null
    kubectl version --request-timeout=10s >/dev/null

    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    chmod 700 "$TMP_DIR"

    RUNTIME_DB_PASSWORD="$(openssl rand -base64 48 | tr -d '\n')"
    jq -n \
      --arg username "sag_runtime" \
      --arg password "$RUNTIME_DB_PASSWORD" \
      '{username:$username,password:$password}' \
      > "$TMP_DIR/database.json"
    chmod 600 "$TMP_DIR/database.json"
    put_json_secret_if_empty "$DATABASE_SECRET" "$TMP_DIR/database.json"
    unset RUNTIME_DB_PASSWORD

    SIGNING_KEY="$(openssl rand -base64 48 | tr -d '\n')"
    jq -n --arg signing_key "$SIGNING_KEY" \
      '{signing_key:$signing_key}' > "$TMP_DIR/signing.json"
    chmod 600 "$TMP_DIR/signing.json"
    put_json_secret_if_empty "$SIGNING_SECRET" "$TMP_DIR/signing.json"
    unset SIGNING_KEY

    GIT_SHA="$(git rev-parse HEAD)"
    IMAGE_TAG="sha-${GIT_SHA:0:12}"
    IMAGE_REF="${ECR_REPOSITORY}:${IMAGE_TAG}"
    REGISTRY_HOST="${ECR_REPOSITORY%%/*}"

    aws ecr get-login-password --region "$REGION" \
      | docker login --username AWS --password-stdin "$REGISTRY_HOST" >/dev/null
    docker build --tag "$IMAGE_REF" .
    docker push "$IMAGE_REF"

    kubectl apply -f k8s/base/namespace.yaml >/dev/null

    TOOL_POLICY="config/tool-execution-policies.json"
    if [ ! -f "$TOOL_POLICY" ]; then
      TOOL_POLICY="config/tool-execution-policies.example.json"
    fi
    if [ ! -f config/security-policies.json ]; then
      echo "ERROR missing_file=config/security-policies.json" >&2
      exit 2
    fi

    kubectl -n "$NAMESPACE" create configmap sag-security-policy \
      --from-file=security-policies.json=config/security-policies.json \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    kubectl -n "$NAMESPACE" create configmap sag-tool-execution-policy \
      --from-file=tool-execution-policies.json="$TOOL_POLICY" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null

    kubectl -n "$NAMESPACE" create configmap sag-cloud-runtime \
      --from-literal=SAG_PROVIDER=mock \
      --from-literal=SAG_ALLOWED_MODELS=openrouter/free \
      --from-literal=SAG_CLIENT_REGISTRY_BACKEND=postgres \
      --from-literal=SAG_USAGE_LEDGER_BACKEND=postgres \
      --from-literal=SAG_RATE_LIMIT_BACKEND=redis \
      --from-literal=SAG_SEMANTIC_PII_BACKEND=spacy \
      --from-literal=SAG_TOOL_EXECUTION_REPLAY_BACKEND=redis \
      --from-literal=SAG_TOOL_EXECUTION_TTL_SECONDS=120 \
      --from-literal=SAG_RUN_MIGRATIONS=false \
      --from-literal=SAG_REDIS_AUTH_MODE=elasticache_iam \
      --from-literal=REDIS_URL="rediss://${VALKEY_HOST}:${VALKEY_PORT}/0" \
      --from-literal=SAG_ELASTICACHE_USER_ID="$VALKEY_USER_ID" \
      --from-literal=SAG_ELASTICACHE_CACHE_NAME="$VALKEY_CACHE_NAME" \
      --from-literal=AWS_REGION="$REGION" \
      --from-literal=SAG_DATABASE_HOST="$DATABASE_HOST" \
      --from-literal=SAG_DATABASE_PORT="$DATABASE_PORT" \
      --from-literal=SAG_DATABASE_NAME="$DATABASE_NAME" \
      --from-literal=SAG_AWS_DATABASE_SECRET_ID="$DATABASE_SECRET" \
      --from-literal=SAG_AWS_TOOL_SIGNING_SECRET_ID="$SIGNING_SECRET" \
      --from-literal=SAG_AWS_RDS_ADMIN_SECRET_ID="$RDS_ADMIN_SECRET" \
      --from-literal=SAG_OTEL_ENABLED=true \
      --from-literal=OTEL_SERVICE_NAME=secure-ai-gateway \
      --from-literal=OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://sag-otel-collector:4318/v1/traces \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null

    kubectl -n "$NAMESPACE" delete job sag-migrate --ignore-not-found >/dev/null
    kubectl kustomize k8s/cloud \
      | sed "s#image: secure-ai-gateway:local#image: ${IMAGE_REF}#g" \
      > "$TMP_DIR/cloud.yaml"

    if grep -q '^kind: Secret$' "$TMP_DIR/cloud.yaml"; then
      echo "ERROR rendered_cloud_secret_detected=true" >&2
      exit 2
    fi

    kubectl apply -f "$TMP_DIR/cloud.yaml" >/dev/null
    kubectl -n "$NAMESPACE" wait \
      --for=condition=complete job/sag-migrate --timeout=600s
    kubectl -n "$NAMESPACE" rollout status deployment/sag-gateway --timeout=600s
    kubectl -n "$NAMESPACE" rollout status deployment/sag-otel-collector --timeout=300s
    kubectl -n "$NAMESPACE" rollout status deployment/sag-prometheus --timeout=300s

    CLIENT_METADATA="$(kubectl -n "$NAMESPACE" exec deploy/sag-gateway -- python -m app.clients list)"
    if printf '%s\n' "$CLIENT_METADATA" \
        | grep -q 'client_id=local-dev .*client_active=true .*key_active=true'; then
      CLIENT_OUTPUT="$(kubectl -n "$NAMESPACE" exec deploy/sag-gateway -- python -m app.clients rotate local-dev)"
    else
      CLIENT_OUTPUT="$(kubectl -n "$NAMESPACE" exec deploy/sag-gateway -- python -m app.clients create local-dev)"
    fi
    CLIENT_KEY="$(printf '%s\n' "$CLIENT_OUTPUT" | sed -n 's/^API key: //p' | tail -n 1)"
    if [ -z "$CLIENT_KEY" ]; then
      echo "ERROR client_bootstrap_failed=true" >&2
      exit 2
    fi
    umask 077
    printf 'SAG_CLIENT_API_KEY=%s\n' "$CLIENT_KEY" > .aws-client.env
    chmod 600 .aws-client.env
    unset CLIENT_KEY CLIENT_OUTPUT

    echo "cloud_deployment=PASS"
    echo "image=$IMAGE_REF"
    echo "gateway_client_credential=.aws-client.env"
    echo "public_gateway_endpoint=none"
    ;;

  *)
    echo "Usage: $0 {bootstrap-plan|bootstrap-apply|plan|infra-apply|deploy}" >&2
    exit 2
    ;;
esac
