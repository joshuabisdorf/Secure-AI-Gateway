#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

for command in docker curl python; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR missing_command=$command" >&2
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR docker_compose_unavailable" >&2
  exit 1
fi

export SAG_PROVIDER=mock
export SAG_ALLOWED_MODELS=mock-model
export SAG_SECURITY_POLICY_HOST_FILE=./config/demo-security-policies.json
export SAG_TOOL_EXECUTION_SIGNING_KEY="${SAG_TOOL_EXECUTION_SIGNING_KEY:-$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)}"

TMP_DIR="$(mktemp -d)"
API_KEY=""
KEY_ID=""
CONTAINER_API_KEY_FILE="/tmp/sag-resilience-client-key-$$"

wait_for_redis() {
  for _ in $(seq 1 30); do
    if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -Fq PONG; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_postgres() {
  for _ in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U sag -d secure_ai_gateway >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cleanup() {
  # Bring PostgreSQL back if a failure occurred during the outage phase so the
  # temporary credential can still be revoked. Do not delete any volumes.
  docker compose start postgres >/dev/null 2>&1 || true
  if wait_for_postgres >/dev/null 2>&1 && [[ -n "$KEY_ID" ]]; then
    for _ in $(seq 1 10); do
      if docker compose exec -T gateway python -m app.clients revoke "$KEY_ID" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi
  docker compose exec -T gateway rm -f "$CONTAINER_API_KEY_FILE" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

chat_status() {
  curl --max-time 15 -sS \
    -o "$TMP_DIR/body" \
    -w '%{http_code}' \
    -H "Authorization: Bearer $API_KEY" \
    -H 'Content-Type: application/json' \
    --data-binary '{"model":"mock-model","messages":[{"role":"user","content":"Resilience probe."}]}' \
    http://127.0.0.1:8000/v1/chat/completions
}

assert_status() {
  local actual="$1"
  local expected="$2"
  local step="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL step=$step expected_status=$expected actual_status=$actual" >&2
    sed -n '1,30p' "$TMP_DIR/body" >&2 || true
    exit 1
  fi
  echo "PASS step=$step status=$actual"
}

wait_for_chat_recovery() {
  local status=""
  for _ in $(seq 1 30); do
    status="$(chat_status || true)"
    if [[ "$status" == "200" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "FAIL step=chat_recovery last_status=${status:-none}" >&2
  sed -n '1,30p' "$TMP_DIR/body" >&2 || true
  return 1
}

echo "=== SECURE AI GATEWAY RESILIENCE SMOKE ==="
echo "provider=mock"
echo "destructive_volume_actions=none"

docker compose up -d --build postgres redis otel-collector gateway >/dev/null

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "FAIL step=gateway_health" >&2
  exit 1
fi
echo "PASS step=gateway_health"

CREATE_OUTPUT="$(
  docker compose exec -T gateway \
    python -m app.clients create portfolio-demo \
    --api-key-file "$CONTAINER_API_KEY_FILE"
)"
KEY_ID="$(printf '%s\n' "$CREATE_OUTPUT" | sed -n 's/^Key ID: //p' | tail -n 1)"
API_KEY="$(docker compose exec -T gateway cat "$CONTAINER_API_KEY_FILE")"
docker compose exec -T gateway rm -f "$CONTAINER_API_KEY_FILE"
if [[ -z "$API_KEY" || -z "$KEY_ID" ]]; then
  echo "FAIL step=create_resilience_client" >&2
  exit 1
fi
echo "PASS step=create_resilience_client key_id=$KEY_ID raw_key_logged=false"

docker compose exec -T redis redis-cli DEL 'sag:rate_limit:portfolio-demo' >/dev/null

STATUS="$(chat_status)"
assert_status "$STATUS" "200" "baseline_chat"

echo "--- injecting Redis outage ---"
docker compose stop redis >/dev/null
STATUS="$(chat_status || true)"
assert_status "$STATUS" "503" "redis_outage_fails_closed"

docker compose start redis >/dev/null
if ! wait_for_redis; then
  echo "FAIL step=redis_recovery backend=unhealthy" >&2
  exit 1
fi
if ! wait_for_chat_recovery; then
  exit 1
fi
echo "PASS step=redis_recovery status=200"

echo "--- injecting PostgreSQL outage ---"
docker compose stop postgres >/dev/null
STATUS="$(chat_status || true)"
assert_status "$STATUS" "503" "postgres_outage_fails_closed"

docker compose start postgres >/dev/null
if ! wait_for_postgres; then
  echo "FAIL step=postgres_recovery backend=unhealthy" >&2
  exit 1
fi
if ! wait_for_chat_recovery; then
  exit 1
fi
echo "PASS step=postgres_recovery status=200"

echo "--- injecting telemetry outage ---"
docker compose stop otel-collector >/dev/null
STATUS="$(chat_status)"
assert_status "$STATUS" "200" "telemetry_outage_not_authorization_dependency"

echo
echo "=== RESULT ==="
echo "secure_ai_gateway_resilience=PASS"
echo "redis_fail_closed=true"
echo "postgres_fail_closed=true"
echo "telemetry_fail_open_for_availability=true"
echo "provider_calls=mock_only"
echo "billable_cloud_resources=0"
echo "temporary_key_revoked_on_exit=true"
echo "stack_left_running=true"
echo "next_cleanup_command=make down"
