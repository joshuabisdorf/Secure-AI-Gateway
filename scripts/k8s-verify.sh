#!/usr/bin/env bash
set -euo pipefail

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

POD_SECURITY_ENFORCE="$(
  kubectl get namespace "$NAMESPACE" \
    -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}'
)"
if [ "$POD_SECURITY_ENFORCE" != "restricted" ]; then
  echo "ERROR pod_security_enforce=$POD_SECURITY_ENFORCE expected=restricted" >&2
  exit 1
fi

NETWORK_POLICY_COUNT="$(
  kubectl -n "$NAMESPACE" get networkpolicy -o name | wc -l | tr -d ' '
)"
if [ "$NETWORK_POLICY_COUNT" -lt 8 ]; then
  echo "ERROR network_policy_count=$NETWORK_POLICY_COUNT expected_at_least=8" >&2
  exit 1
fi
if ! kubectl -n "$NAMESPACE" get networkpolicy sag-default-deny >/dev/null 2>&1; then
  echo "ERROR default_deny_network_policy=missing" >&2
  exit 1
fi

echo "pod_security_enforce=restricted network_policies=$NETWORK_POLICY_COUNT"

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

CPU_LIMIT="$(
  kubectl -n "$NAMESPACE" get deployment sag-gateway \
    -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}'
)"
MEMORY_LIMIT="$(
  kubectl -n "$NAMESPACE" get deployment sag-gateway \
    -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'
)"
CPU_REQUEST="$(
  kubectl -n "$NAMESPACE" get deployment sag-gateway \
    -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}'
)"
MEMORY_REQUEST="$(
  kubectl -n "$NAMESPACE" get deployment sag-gateway \
    -o jsonpath='{.spec.template.spec.containers[0].resources.requests.memory}'
)"
if [ -z "$CPU_LIMIT" ] || [ -z "$MEMORY_LIMIT" ] || [ -z "$CPU_REQUEST" ] || [ -z "$MEMORY_REQUEST" ]; then
  echo "ERROR gateway_resource_bounds=missing" >&2
  exit 1
fi
echo "gateway_resources=bounded cpu_request=$CPU_REQUEST memory_request=$MEMORY_REQUEST cpu_limit=$CPU_LIMIT memory_limit=$MEMORY_LIMIT"

cleanup() {
  kubectl -n "$NAMESPACE" delete pod sag-network-deny-probe --ignore-not-found \
    --wait=false >/dev/null 2>&1 || true
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
if ! python - "$PROM_COUNT" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) >= 1 else 1)
PY
then
  echo "ERROR prometheus_gateway_target_missing" >&2
  exit 1
fi

sleep 8
TRACE_LOGS="$(kubectl -n "$NAMESPACE" logs deployment/sag-otel-collector --since=30s 2>&1 || true)"
if printf '%s\n' "$TRACE_LOGS" | grep -q 'otelcol.signal.*traces'; then
  echo "otel_trace_export=observed"
else
  echo "otel_trace_export=missing"
  exit 1
fi

REDIS_POD_IP="$(
  kubectl -n "$NAMESPACE" get pod \
    -l app.kubernetes.io/component=redis \
    -o jsonpath='{.items[0].status.podIP}'
)"
cat > /tmp/sag-network-deny-probe.yaml <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: sag-network-deny-probe
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: secure-ai-gateway
    app.kubernetes.io/component: network-probe
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: probe
      image: secure-ai-gateway:local
      imagePullPolicy: IfNotPresent
      command:
        - python
        - -c
        - "import socket; socket.create_connection(('$REDIS_POD_IP', 6379), 2)"
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop:
            - ALL
EOF
kubectl apply -f /tmp/sag-network-deny-probe.yaml >/dev/null
PROBE_PHASE=""
for _ in $(seq 1 30); do
  PROBE_PHASE="$(
    kubectl -n "$NAMESPACE" get pod sag-network-deny-probe \
      -o jsonpath='{.status.phase}' 2>/dev/null || true
  )"
  if [ "$PROBE_PHASE" = "Failed" ] || [ "$PROBE_PHASE" = "Succeeded" ]; then
    break
  fi
  sleep 1
done
if [ "$PROBE_PHASE" != "Failed" ]; then
  echo "ERROR default_deny_probe_phase=$PROBE_PHASE expected=Failed" >&2
  exit 1
fi
echo "default_deny_network_policy=enforced"
kubectl -n "$NAMESPACE" delete pod sag-network-deny-probe --wait=false >/dev/null

DELETED_POD="${GATEWAY_PODS[1]}"
kubectl -n "$NAMESPACE" delete pod "$DELETED_POD" --wait=false >/dev/null
SURVIVOR_STATUS="$(send_request 18001 sag-k8s-survivor)"
if [ "$SURVIVOR_STATUS" != "200" ]; then
  echo "ERROR surviving_replica_status=$SURVIVOR_STATUS expected=200" >&2
  exit 1
fi
kubectl -n "$NAMESPACE" rollout status deployment/sag-gateway --timeout=180s >/dev/null

mapfile -t RESCHEDULED_PODS < <(
  kubectl -n "$NAMESPACE" get pods \
    -l app.kubernetes.io/component=gateway \
    --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
)
if [ "${#RESCHEDULED_PODS[@]}" -lt 2 ]; then
  echo "ERROR rescheduled_gateway_replicas=${#RESCHEDULED_PODS[@]} expected=2" >&2
  exit 1
fi
REPLACEMENT_POD=""
for pod in "${RESCHEDULED_PODS[@]}"; do
  if [ "$pod" != "${GATEWAY_PODS[0]}" ] && [ "$pod" != "$DELETED_POD" ]; then
    REPLACEMENT_POD="$pod"
    break
  fi
done
if [ -z "$REPLACEMENT_POD" ]; then
  echo "ERROR replacement_gateway_pod=missing" >&2
  exit 1
fi

kubectl -n "$NAMESPACE" port-forward "pod/$REPLACEMENT_POD" 18003:8000 \
  >/tmp/sag-k8s-replacement-port-forward.log 2>&1 &
PF_PIDS+=("$!")
ready=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:18003/health >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "ERROR replacement_port_forward_not_ready=18003" >&2
  exit 1
fi
REPLACEMENT_STATUS="$(send_request 18003 sag-k8s-replacement)"
if [ "$REPLACEMENT_STATUS" != "200" ]; then
  echo "ERROR replacement_replica_status=$REPLACEMENT_STATUS expected=200" >&2
  exit 1
fi
echo "replica_rescheduling=PASS deleted=$DELETED_POD replacement=$REPLACEMENT_POD survivor_status=$SURVIVOR_STATUS replacement_status=$REPLACEMENT_STATUS"

LOAD_STATUS_FILE="/tmp/sag-k8s-load-statuses.txt"
: > "$LOAD_STATUS_FILE"
LOAD_PIDS=()
for request_number in $(seq 1 8); do
  if [ $((request_number % 2)) -eq 0 ]; then
    port=18001
  else
    port=18003
  fi
  (
    status="$(send_request "$port" "sag-k8s-load-$request_number" || printf '000')"
    printf '%s\n' "$status" >> "$LOAD_STATUS_FILE"
  ) &
  LOAD_PIDS+=("$!")
done
for pid in "${LOAD_PIDS[@]}"; do
  wait "$pid"
done

LOAD_TOTAL="$(wc -l < "$LOAD_STATUS_FILE" | tr -d ' ')"
LOAD_BAD="$(grep -Evc '^(200|429)$' "$LOAD_STATUS_FILE" || true)"
LOAD_200="$(grep -c '^200$' "$LOAD_STATUS_FILE" || true)"
LOAD_429="$(grep -c '^429$' "$LOAD_STATUS_FILE" || true)"
if [ "$LOAD_TOTAL" -ne 8 ] || [ "$LOAD_BAD" -ne 0 ]; then
  echo "ERROR bounded_load total=$LOAD_TOTAL bad=$LOAD_BAD" >&2
  cat "$LOAD_STATUS_FILE" >&2
  exit 1
fi

RESTART_COUNTS="$(
  kubectl -n "$NAMESPACE" get pods \
    -l app.kubernetes.io/component=gateway \
    -o jsonpath='{range .items[*].status.containerStatuses[*]}{.restartCount}{"\n"}{end}'
)"
RESTART_TOTAL="$(printf '%s\n' "$RESTART_COUNTS" | awk '{sum += $1} END {print sum + 0}')"
OOM_KILLED="$(
  kubectl -n "$NAMESPACE" get pods \
    -l app.kubernetes.io/component=gateway \
    -o json \
    | python -c '
import json, sys
payload = json.load(sys.stdin)
reasons = []
for pod in payload.get("items", []):
    for status in pod.get("status", {}).get("containerStatuses", []):
        terminated = status.get("lastState", {}).get("terminated", {})
        if terminated.get("reason") == "OOMKilled":
            reasons.append(pod["metadata"]["name"])
print(len(reasons))
'
)"
if [ "$RESTART_TOTAL" -ne 0 ] || [ "$OOM_KILLED" -ne 0 ]; then
  echo "ERROR bounded_load_restarts=$RESTART_TOTAL oom_killed=$OOM_KILLED" >&2
  exit 1
fi
echo "bounded_load=PASS requests=$LOAD_TOTAL status_200=$LOAD_200 status_429=$LOAD_429 restarts=$RESTART_TOTAL oom_killed=$OOM_KILLED"

echo "gateway_replicas=2 shared_redis=true shared_postgres=true prometheus=true otel=true network_policy=true pod_security=restricted rescheduling=true resource_bounds=true"
