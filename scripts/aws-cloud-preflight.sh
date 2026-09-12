#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR missing_command=$1" >&2
    exit 2
  fi
}

for command in aws terraform docker kubectl jq curl; do
  require_command "$command"
done

if [ "$(terraform version -json | jq -r '.terraform_version')" != "1.16.2" ]; then
  echo "ERROR terraform_version_must_be=1.16.2" >&2
  exit 2
fi

IDENTITY_JSON="$(aws sts get-caller-identity --output json)"
ACCOUNT_ID="$(printf '%s' "$IDENTITY_JSON" | jq -r '.Account')"
CALLER_ARN="$(printf '%s' "$IDENTITY_JSON" | jq -r '.Arn')"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

if [ ! -f terraform/bootstrap/terraform.tfvars ]; then
  umask 077
  cat > terraform/bootstrap/terraform.tfvars <<EOF
aws_region        = "$REGION"
state_bucket_name = "secure-ai-gateway-tfstate-${ACCOUNT_ID}-${REGION}"
EOF
  chmod 600 terraform/bootstrap/terraform.tfvars
  echo "created_local_file=terraform/bootstrap/terraform.tfvars"
fi

if [ ! -f terraform/aws/dev.tfvars ]; then
  cp terraform/aws/dev.tfvars.example terraform/aws/dev.tfvars
  chmod 600 terraform/aws/dev.tfvars
  echo "created_local_file=terraform/aws/dev.tfvars"
fi

if [ ! -f config/security-policies.json ]; then
  echo "ERROR missing_file=config/security-policies.json" >&2
  exit 2
fi

PUBLIC_IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
SUGGESTED_ADMIN_ARN=""
case "$CALLER_ARN" in
  arn:aws:sts::*:assumed-role/*/*)
    ACCOUNT_PART="${CALLER_ARN#arn:aws:sts::}"
    ACCOUNT_PART="${ACCOUNT_PART%%:*}"
    ROLE_AND_SESSION="${CALLER_ARN#*:assumed-role/}"
    ROLE_PATH="${ROLE_AND_SESSION%/*}"
    SUGGESTED_ADMIN_ARN="arn:aws:iam::${ACCOUNT_PART}:role/${ROLE_PATH}"
    ;;
  arn:aws:iam::*:role/*)
    SUGGESTED_ADMIN_ARN="$CALLER_ARN"
    ;;
esac

echo "=== AWS IDENTITY ==="
echo "account_id=$ACCOUNT_ID"
echo "caller_arn=$CALLER_ARN"
echo "region=$REGION"
if [ -n "$SUGGESTED_ADMIN_ARN" ]; then
  echo "suggested_eks_admin_role_arn=$SUGGESTED_ADMIN_ARN"
else
  echo "suggested_eks_admin_role_arn=UNAVAILABLE_USE_AN_IAM_ROLE"
fi
if [[ "$PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "suggested_eks_public_access_cidr=${PUBLIC_IP}/32"
else
  echo "suggested_eks_public_access_cidr=UNAVAILABLE_USE_PRIVATE_NETWORK_ACCESS"
fi

echo
echo "=== TERRAFORM FORMAT ==="
terraform fmt -check -diff -recursive terraform

echo
echo "=== TERRAFORM VALIDATION ==="
terraform -chdir=terraform/bootstrap init -backend=false -input=false >/dev/null
terraform -chdir=terraform/bootstrap validate -no-color
terraform -chdir=terraform/aws init -backend=false -input=false >/dev/null
terraform -chdir=terraform/aws validate -no-color

echo
echo "=== KUBERNETES CLOUD RENDER ==="
kubectl kustomize k8s/cloud >/tmp/sag-cloud-rendered.yaml
if grep -q '^kind: Secret$' /tmp/sag-cloud-rendered.yaml; then
  echo "ERROR tracked_cloud_secret_detected=true" >&2
  exit 2
fi
echo "cloud_kustomize=OK"
echo "tracked_kubernetes_secrets=0"

echo
echo "=== BOOTSTRAP PLAN ==="
terraform -chdir=terraform/bootstrap plan \
  -input=false \
  -no-color \
  -var-file=terraform.tfvars \
  -out=/tmp/sag-bootstrap.tfplan

echo
echo "=== DEPLOYMENT INPUT CHECK ==="
if grep -Eq '^[[:space:]]*eks_admin_role_arn[[:space:]]*=' terraform/aws/dev.tfvars; then
  echo "eks_admin_role_configured=yes"
else
  echo "eks_admin_role_configured=no"
fi

if grep -Eq '^[[:space:]]*eks_public_access_cidrs[[:space:]]*=[[:space:]]*\[[^]]+\]' terraform/aws/dev.tfvars; then
  echo "eks_api_workstation_access=public_cidr_configured"
else
  echo "eks_api_workstation_access=private_only"
fi

echo
echo "=== RESULT ==="
echo "cloud_preflight=PASS"
echo "aws_resources_created=0"
echo "next_safe_step=review_bootstrap_plan_and_access_settings"
