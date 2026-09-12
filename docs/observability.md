# Observability

Secure AI Gateway exposes bounded Prometheus metrics and optional OpenTelemetry traces without treating telemetry as part of an authorization decision.

Observability is intentionally metadata-only. Prompts, response bodies, raw PII, gateway/provider credentials, execution tickets, tool arguments, and tool results are not recorded as metric labels or trace attributes.

## Local stack

Compose runs two additional observability services:

```text
gateway /metrics  ---> Prometheus :9090
       |
       +-- OTLP/HTTP traces ---> OpenTelemetry Collector :4318
```

Pinned local images:

```text
prom/prometheus:v3.13.2
otel/opentelemetry-collector-contrib:0.160.0
```

Prometheus stores local time-series data in the named `sag_prometheus_data` volume. Normal `docker compose down` preserves it.

The OpenTelemetry Collector currently exports spans to its debug exporter. This verifies trace production and creates the boundary where a later Jaeger/Tempo/vendor backend can be attached without changing gateway instrumentation.

## Prometheus endpoint

The gateway exposes:

```text
GET /metrics
```

This endpoint is intentionally unauthenticated for scraper compatibility and must remain on a trusted/internal network boundary in production. The local Compose gateway is bound only to `127.0.0.1:8000`.

Prometheus scrapes the gateway every five seconds using:

```text
observability/prometheus.yml
```

Primary metric families include:

```text
sag_http_requests_total
sag_http_request_duration_seconds
sag_provider_requests_total
sag_provider_request_duration_seconds
sag_security_decisions_total
sag_pii_findings_total
sag_prompt_injection_findings_total
sag_tool_authorization_decisions_total
sag_usage_tokens_total
sag_usage_cost_usd_total
sag_backend_failures_total
```

Protected HTTP request metrics use only method, a fixed known route, and status class. Arbitrary paths become the fixed route label `unmatched`.

Security counters are derived from the already-sanitized audit event stream. They deliberately do not label by:

- client ID;
- API-key ID;
- request ID;
- requested/resolved model;
- tool name;
- denial reason;
- prompt/message text.

This keeps metric cardinality bounded and prevents common telemetry leakage paths.

## Example PromQL

Protected gateway request rate:

```promql
sum(rate(sag_http_requests_total[5m])) by (route, status_class)
```

95th percentile protected request latency:

```promql
histogram_quantile(
  0.95,
  sum(rate(sag_http_request_duration_seconds_bucket[5m])) by (le, route)
)
```

Provider error rate:

```promql
sum(rate(sag_provider_requests_total{outcome="error"}[5m])) by (provider)
```

Policy denials:

```promql
sum(rate(sag_security_decisions_total{outcome="deny"}[5m])) by (event)
```

PII detection activity:

```promql
sum(rate(sag_pii_findings_total[5m])) by (pii_type)
```

Execution-time tool authorization decisions:

```promql
sum(rate(sag_tool_authorization_decisions_total{stage="execution"}[5m])) by (outcome, risk)
```

Persisted token usage:

```promql
sum(rate(sag_usage_tokens_total[5m])) by (provider)
```

## OpenTelemetry tracing

Tracing is controlled by:

```dotenv
SAG_OTEL_ENABLED=true
OTEL_SERVICE_NAME=secure-ai-gateway
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4318/v1/traces
```

Compose supplies those values automatically unless overridden. Host-run development defaults tracing off in `.env.example` so starting Uvicorn does not assume a collector exists.

The gateway uses the OpenTelemetry SDK and OTLP/HTTP exporter. It accepts standard incoming W3C trace context and creates:

- one server span around each protected chat/tool-execution authorization request;
- one child client span around each upstream provider request.

When tracing is enabled, protected responses also receive:

```text
X-Trace-ID: <32 lowercase hexadecimal characters>
```

Trace attributes are deliberately restricted to bounded routing/provider metadata plus the existing gateway request correlation ID. Prompt bodies, response bodies, arbitrary request headers, credentials, PII values, and tool arguments are not attached.

`/health` and `/metrics` are not traced through the protected-request dependency. This avoids scrape/health traffic dominating application traces.

## Failure behavior

Prometheus recording is process-local and does not perform network I/O.

OTLP trace export is asynchronous through the OpenTelemetry batch span processor. Collector/exporter failure must not authorize, deny, or otherwise change gateway security behavior. Audit logging remains the authoritative security event stream.

Likewise, failure to derive a Prometheus counter from an audit event is deliberately ignored by the audit emitter so telemetry cannot block a security decision or suppress the JSON audit record.

## Local verification

Start or rebuild the stack:

```bash
docker compose up -d --build
docker compose ps
```

After sending a normal authenticated gateway request, inspect the raw metrics:

```bash
curl -sS http://127.0.0.1:8000/metrics | grep '^sag_'
```

Prometheus is available locally at:

```text
http://127.0.0.1:9090
```

Check the gateway target from Prometheus with:

```text
up{job="secure-ai-gateway"}
```

A healthy scrape target returns `1`.

To verify exported traces, inspect the collector debug output:

```bash
docker compose logs --tail=100 otel-collector
```

After a protected request you should see received/exported span activity for the gateway request and provider child span. Do not use collector debug export as a production trace backend.

## Production direction

For Kubernetes/cloud deployment:

1. keep `/metrics` reachable only from the monitoring network/service account;
2. send OTLP to a dedicated collector rather than directly to a vendor backend;
3. use TLS/authentication for telemetry crossing trust boundaries;
4. replace the debug exporter with a persistent trace backend;
5. keep metric labels bounded as new controls are added;
6. alert on backend failures, provider errors, unusual deny rates, and budget/rate-limit pressure without introducing customer identifiers into labels.
