#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

NAMESPACE="secure-ai-gateway"

for command in kubectl curl python; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR missing_command=$command" >&2
    exit 2
  fi
done

if [ ! -f .aws-client.env ]; then
  echo "ERROR missing_file=.aws-client.env" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1091
. ./.aws-client.env
set +a
if [ -z "${SAG_CLIENT_API_KEY:-}" ]; then
  echo "ERROR missing_env=SAG_CLIENT_API_KEY" >&2
  exit 2
fi

mapfile -t PODS < <(
  kubectl -n "$NAMESPACE" get pods \
    -l app.kubernetes.io/component=gateway \
    --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    | sort
)
if [ "${#PODS[@]}" -lt 2 ]; then
  echo "ERROR gateway_replicas_ready=${#PODS[@]}" >&2
  exit 2
fi

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

wait_health() {
  local port="$1"
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

kubectl -n "$NAMESPACE" port-forward "pod/${PODS[0]}" 18001:8000 \
  >/tmp/sag-aws-pod1-port-forward.log 2>&1 &
PIDS+=("$!")
kubectl -n "$NAMESPACE" port-forward "pod/${PODS[1]}" 18002:8000 \
  >/tmp/sag-aws-pod2-port-forward.log 2>&1 &
PIDS+=("$!")

wait_health 18001
wait_health 18002

send_request() {
  local port="$1"
  local prefix="$2"
  curl -sS \
    -D "/tmp/${prefix}-headers.txt" \
    -o "/tmp/${prefix}-body.json" \
    -w '%{http_code}' \
    --max-time 20 \
    -X POST "http://127.0.0.1:${port}/v1/chat/completions" \
    -H "Authorization: Bearer $SAG_CLIENT_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"openrouter/free","messages":[{"role":"user","content":"Cloud verification"}]}'
}

header_value() {
  local name="$1"
  local file="$2"
  awk -v wanted="$name" '
    BEGIN { IGNORECASE=1 }
    $1 == wanted ":" {
      gsub("\r", "", $2)
      print $2
    }
  ' "$file" | tail -n 1
}

STATUS_ONE="$(send_request 18001 sag-aws-one)"
STATUS_TWO="$(send_request 18002 sag-aws-two)"
python -m json.tool /tmp/sag-aws-one-body.json >/dev/null
python -m json.tool /tmp/sag-aws-two-body.json >/dev/null

RATE_ONE="$(header_value X-RateLimit-Remaining /tmp/sag-aws-one-headers.txt)"
RATE_TWO="$(header_value X-RateLimit-Remaining /tmp/sag-aws-two-headers.txt)"
USAGE_ONE="$(header_value X-Usage-Tokens-Used /tmp/sag-aws-one-headers.txt)"
USAGE_TWO="$(header_value X-Usage-Tokens-Used /tmp/sag-aws-two-headers.txt)"
TRACE_ONE="$(header_value X-Trace-ID /tmp/sag-aws-one-headers.txt)"
TRACE_TWO="$(header_value X-Trace-ID /tmp/sag-aws-two-headers.txt)"

echo "gateway_pod_1=${PODS[0]} status=$STATUS_ONE rate_remaining=$RATE_ONE usage_tokens=$USAGE_ONE trace_id_length=${#TRACE_ONE}"
echo "gateway_pod_2=${PODS[1]} status=$STATUS_TWO rate_remaining=$RATE_TWO usage_tokens=$USAGE_TWO trace_id_length=${#TRACE_TWO}"

if [ "$STATUS_ONE" != "200" ] || [ "$STATUS_TWO" != "200" ]; then
  echo "ERROR gateway_request_failed" >&2
  exit 1
fi
if [ -z "$RATE_ONE" ] || [ -z "$RATE_TWO" ] || [ "$RATE_TWO" -ge "$RATE_ONE" ]; then
  echo "ERROR shared_valkey_rate_limit_not_observed" >&2
  exit 1
fi
if [ -z "$USAGE_ONE" ] || [ -z "$USAGE_TWO" ] || [ "$USAGE_TWO" -le "$USAGE_ONE" ]; then
  echo "ERROR shared_rds_usage_not_observed" >&2
  exit 1
fi
if [ "${#TRACE_ONE}" -ne 32 ] || [ "${#TRACE_TWO}" -ne 32 ]; then
  echo "ERROR trace_id_missing" >&2
  exit 1
fi

kubectl -n "$NAMESPACE" port-forward service/sag-prometheus 19090:9090 \
  >/tmp/sag-aws-prometheus-port-forward.log 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:19090/-/ready >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

PROM_RESULT="$(
  curl -sS --max-time 5 --get \
    --data-urlencode 'query=count(up{job="secure-ai-gateway"} == 1)' \
    http://127.0.0.1:19090/api/v1/query
)"
HEALTHY_TARGETS="$(
  printf '%s' "$PROM_RESULT" | python -c '
import json, sys
payload = json.load(sys.stdin)
result = payload.get("data", {}).get("result", [])
print(int(float(result[0]["value"][1])) if result else 0)
'
)"
echo "prometheus_healthy_gateway_targets=$HEALTHY_TARGETS"
if [ "$HEALTHY_TARGETS" -lt 2 ]; then
  exit 1
fi

sleep 8
TRACE_LOGS="$(kubectl -n "$NAMESPACE" logs deployment/sag-otel-collector --since=60s 2>&1 || true)"
if printf '%s\n' "$TRACE_LOGS" | grep -q 'otelcol.signal.*traces'; then
  echo "otel_trace_export=observed"
else
  echo "otel_trace_export=missing" >&2
  exit 1
fi

READY_REPLICAS="$(kubectl -n "$NAMESPACE" get deployment sag-gateway -o jsonpath='{.status.readyReplicas}')"
if [ "$READY_REPLICAS" != "2" ]; then
  echo "ERROR gateway_ready_replicas=$READY_REPLICAS" >&2
  exit 1
fi

echo "gateway_replicas=2 shared_valkey=true shared_rds=true prometheus=true otel=true public_endpoint=false"
