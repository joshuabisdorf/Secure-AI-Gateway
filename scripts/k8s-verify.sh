#!/usr/bin/env bash
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

NAMESPACE="secure-ai-gateway"

if [ ! -f .k8s-client.env ]; then
  echo "ERROR missing_file=.k8s-client.env" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1091
. ./.k8s-client.env
set +a

if [ -z "${SAG_CLIENT_API_KEY:-}" ]; then
  echo "ERROR missing_env=SAG_CLIENT_API_KEY" >&2
  exit 2
fi

mapfile -t GATEWAY_PODS < <(
  kubectl -n "$NAMESPACE" get pods \
    -l app.kubernetes.io/component=gateway \
    --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
)

if [ "${#GATEWAY_PODS[@]}" -lt 2 ]; then
  echo "ERROR expected_gateway_replicas=2 actual=${#GATEWAY_PODS[@]}" >&2
  exit 2
fi

cleanup() {
  for pid in "${PF_PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT
PF_PIDS=()

kubectl -n "$NAMESPACE" port-forward "pod/${GATEWAY_PODS[0]}" 18001:8000 \
  >/tmp/sag-k8s-pod1-port-forward.log 2>&1 &
PF_PIDS+=("$!")
kubectl -n "$NAMESPACE" port-forward "pod/${GATEWAY_PODS[1]}" 18002:8000 \
  >/tmp/sag-k8s-pod2-port-forward.log 2>&1 &
PF_PIDS+=("$!")
kubectl -n "$NAMESPACE" port-forward service/sag-prometheus 19090:9090 \
  >/tmp/sag-k8s-prometheus-port-forward.log 2>&1 &
PF_PIDS+=("$!")

for port in 18001 18002; do
  ready=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    echo "ERROR port_forward_not_ready=$port" >&2
    exit 2
  fi
done

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
    -d '{
      "model": "openrouter/free",
      "messages": [
        {
          "role": "user",
          "content": "Reply with exactly: Kubernetes shared state works"
        }
      ]
    }'
}

STATUS_ONE="$(send_request 18001 sag-k8s-one)"
STATUS_TWO="$(send_request 18002 sag-k8s-two)"

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

RATE_ONE="$(header_value X-RateLimit-Remaining /tmp/sag-k8s-one-headers.txt)"
RATE_TWO="$(header_value X-RateLimit-Remaining /tmp/sag-k8s-two-headers.txt)"
USAGE_ONE="$(header_value X-Usage-Tokens-Used /tmp/sag-k8s-one-headers.txt)"
USAGE_TWO="$(header_value X-Usage-Tokens-Used /tmp/sag-k8s-two-headers.txt)"
TRACE_ONE="$(header_value X-Trace-ID /tmp/sag-k8s-one-headers.txt)"
TRACE_TWO="$(header_value X-Trace-ID /tmp/sag-k8s-two-headers.txt)"

echo "gateway_pod_1=${GATEWAY_PODS[0]} status=$STATUS_ONE rate_remaining=$RATE_ONE usage_tokens=$USAGE_ONE trace_id_length=${#TRACE_ONE}"
echo "gateway_pod_2=${GATEWAY_PODS[1]} status=$STATUS_TWO rate_remaining=$RATE_TWO usage_tokens=$USAGE_TWO trace_id_length=${#TRACE_TWO}"

if [ "$STATUS_ONE" != "200" ] || [ "$STATUS_TWO" != "200" ]; then
  echo "ERROR gateway_request_failed" >&2
  exit 1
fi

if [ -z "$RATE_ONE" ] || [ -z "$RATE_TWO" ] || [ "$RATE_TWO" -ge "$RATE_ONE" ]; then
  echo "ERROR shared_redis_rate_limit_not_observed" >&2
  exit 1
fi

if [ -z "$USAGE_ONE" ] || [ -z "$USAGE_TWO" ] || [ "$USAGE_TWO" -le "$USAGE_ONE" ]; then
  echo "ERROR shared_postgres_usage_not_observed" >&2
  exit 1
fi

if [ "${#TRACE_ONE}" -ne 32 ] || [ "${#TRACE_TWO}" -ne 32 ]; then
  echo "ERROR trace_id_missing" >&2
  exit 1
fi

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
PROM_COUNT="$(
  printf '%s' "$PROM_RESULT" | python -c '
import json, sys
payload = json.load(sys.stdin)
result = payload.get("data", {}).get("result", [])
print(result[0]["value"][1] if result else "0")
'
)"

echo "prometheus_healthy_gateway_targets=$PROM_COUNT"

sleep 8
TRACE_LOGS="$(kubectl -n "$NAMESPACE" logs deployment/sag-otel-collector --since=30s 2>&1 || true)"
if printf '%s\n' "$TRACE_LOGS" | grep -q 'otelcol.signal.*traces'; then
  echo "otel_trace_export=observed"
else
  echo "otel_trace_export=missing"
  exit 1
fi

echo "gateway_replicas=2 shared_redis=true shared_postgres=true prometheus=true otel=true"
