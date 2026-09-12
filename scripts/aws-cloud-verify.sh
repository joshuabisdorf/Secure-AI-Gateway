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

request_pod() {
  local pod="$1"
  local port="$2"
  local label="$3"
  local headers="/tmp/sag-aws-${label}-headers.txt"
  local body="/tmp/sag-aws-${label}-body.json"

  kubectl -n "$NAMESPACE" port-forward "pod/${pod}" "${port}:8000" \
    >/tmp/sag-aws-${label}-port-forward.log 2>&1 &
  local pf_pid=$!
  PIDS+=("$pf_pid")
  wait_health "$port"

  curl -fsS \
    -D "$headers" \
    -o "$body" \
    --max-time 20 \
    -X POST "http://127.0.0.1:${port}/v1/chat/completions" \
    -H "Authorization: Bearer $SAG_CLIENT_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"openrouter/free","messages":[{"role":"user","content":"Cloud verification"}]}'

  local status rate_remaining usage_tokens trace_id
  status="$(awk 'NR==1 {print $2}' "$headers")"
  rate_remaining="$(awk 'BEGIN{IGNORECASE=1}/^x-ratelimit-remaining:/{gsub("\r",""); print $2}' "$headers")"
  usage_tokens="$(awk 'BEGIN{IGNORECASE=1}/^x-usage-tokens-used:/{gsub("\r",""); print $2}' "$headers")"
  trace_id="$(awk 'BEGIN{IGNORECASE=1}/^x-trace-id:/{gsub("\r",""); print $2}' "$headers")"

  python -m json.tool "$body" >/dev/null
  echo "${label}=${pod} status=${status} rate_remaining=${rate_remaining} usage_tokens=${usage_tokens} trace_id_length=${#trace_id}"

  if [ "$status" != "200" ] || [ "${#trace_id}" -ne 32 ]; then
    return 1
  fi
}

request_pod "${PODS[0]}" 18001 gateway_pod_1
request_pod "${PODS[1]}" 18002 gateway_pod_2

kubectl -n "$NAMESPACE" port-forward service/sag-prometheus 19090:9090 \
  >/tmp/sag-aws-prometheus-port-forward.log 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:19090/-/ready >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

PROM_RESULT="$(curl -fsS --max-time 5 \
  'http://127.0.0.1:19090/api/v1/query?query=count%28up%7Bjob%3D%22secure-ai-gateway%22%7D%20%3D%3D%201%29')"
HEALTHY_TARGETS="$(printf '%s' "$PROM_RESULT" | python -c 'import json,sys; d=json.load(sys.stdin); r=d["data"]["result"]; print(int(float(r[0]["value"][1])) if r else 0)')"
echo "prometheus_healthy_gateway_targets=$HEALTHY_TARGETS"
if [ "$HEALTHY_TARGETS" -lt 2 ]; then
  exit 1
fi

sleep 6
if kubectl -n "$NAMESPACE" logs deployment/sag-otel-collector --since=90s 2>&1 \
    | grep -Eq 'spans": [1-9]|Spans'; then
  echo "otel_trace_export=observed"
else
  echo "otel_trace_export=missing" >&2
  exit 1
fi

READY_REPLICAS="$(kubectl -n "$NAMESPACE" get deployment sag-gateway -o jsonpath='{.status.readyReplicas}')"
echo "gateway_replicas=$READY_REPLICAS shared_valkey=true shared_rds=true prometheus=true otel=true public_endpoint=false"

if [ "$READY_REPLICAS" != "2" ]; then
  exit 1
fi
