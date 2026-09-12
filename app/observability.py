import os
import re
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterator, Mapping

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

_service_name = "secure-ai-gateway"
_safe_label_pattern = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_known_routes = frozenset(
    {
        "/health",
        "/metrics",
        "/v1/chat/completions",
        "/v1/tool-executions/authorize",
    }
)
_known_pii_types = frozenset(
    {
        "email",
        "ssn",
        "phone",
        "payment_card",
        "person_name",
        "personal_location",
        "date_of_birth",
        "street_address",
    }
)
_known_injection_indicators = frozenset(
    {
        "instruction_override",
        "system_prompt_extraction",
        "role_impersonation",
        "safety_bypass",
        "secret_exfiltration",
        "encoded_payload",
    }
)

metrics_registry = CollectorRegistry(auto_describe=True)

http_requests_total = Counter(
    "sag_http_requests_total",
    "Gateway HTTP requests by method, bounded route, and status class.",
    ("method", "route", "status_class"),
    registry=metrics_registry,
)
http_request_duration_seconds = Histogram(
    "sag_http_request_duration_seconds",
    "Gateway HTTP request latency in seconds by method and bounded route.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=metrics_registry,
)
provider_requests_total = Counter(
    "sag_provider_requests_total",
    "Upstream provider requests by provider and outcome.",
    ("provider", "outcome"),
    registry=metrics_registry,
)
provider_request_duration_seconds = Histogram(
    "sag_provider_request_duration_seconds",
    "Upstream provider request latency in seconds by provider.",
    ("provider",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=metrics_registry,
)
security_decisions_total = Counter(
    "sag_security_decisions_total",
    "Security-relevant gateway decisions by bounded event and outcome.",
    ("event", "outcome"),
    registry=metrics_registry,
)
pii_findings_total = Counter(
    "sag_pii_findings_total",
    "PII findings by safe PII type.",
    ("pii_type",),
    registry=metrics_registry,
)
prompt_injection_findings_total = Counter(
    "sag_prompt_injection_findings_total",
    "Prompt-injection findings by deterministic indicator.",
    ("indicator",),
    registry=metrics_registry,
)
tool_authorization_decisions_total = Counter(
    "sag_tool_authorization_decisions_total",
    "Tool authorization decisions by stage, outcome, and risk class.",
    ("stage", "outcome", "risk"),
    registry=metrics_registry,
)
usage_tokens_total = Counter(
    "sag_usage_tokens_total",
    "Persisted provider-reported token usage by provider.",
    ("provider",),
    registry=metrics_registry,
)
usage_cost_usd_total = Counter(
    "sag_usage_cost_usd_total",
    "Persisted provider-reported cost in USD by provider.",
    ("provider",),
    registry=metrics_registry,
)
backend_failures_total = Counter(
    "sag_backend_failures_total",
    "Shared-backend or provider failures by bounded component.",
    ("component",),
    registry=metrics_registry,
)

_owned_tracer_provider: TracerProvider | None = None


def _env_enabled(name: str, default: bool = False) -> bool:
    """
    RME

    Requires:
        - Environment value, when present, is intended to be a boolean toggle.

    Modifies:
        - Nothing.

    Effects:
        - Interprets common true values without accepting arbitrary configuration.

    Inputs:
        - name: Environment variable name.
        - default: Value used when the variable is absent.

    Outputs:
        - Parsed boolean toggle.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def configure_tracing() -> None:
    """
    RME

    Requires:
        - OTLP environment variables may identify a trace collector when tracing is enabled.

    Modifies:
        - Global OpenTelemetry tracer provider once per process when enabled.

    Effects:
        - Leaves tracing as the OpenTelemetry no-op provider by default.
        - Configures batched OTLP/HTTP trace export when SAG_OTEL_ENABLED is true.

    Inputs:
        - None.

    Outputs:
        - None.
    """
    global _owned_tracer_provider
    if not _env_enabled("SAG_OTEL_ENABLED") or _owned_tracer_provider is not None:
        return

    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", _service_name),
            "service.version": "0.1.0",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _owned_tracer_provider = provider


def shutdown_tracing() -> None:
    """
    RME

    Requires:
        - configure_tracing may have created a process-owned tracer provider.

    Modifies:
        - OpenTelemetry exporter worker state during shutdown.

    Effects:
        - Flushes and shuts down the process-owned tracer provider when present.

    Inputs:
        - None.

    Outputs:
        - None.
    """
    if _owned_tracer_provider is not None:
        _owned_tracer_provider.shutdown()


configure_tracing()
tracer = trace.get_tracer(_service_name)


def bounded_route(path: str) -> str:
    """
    RME

    Requires:
        - path is an HTTP request path.

    Modifies:
        - Nothing.

    Effects:
        - Prevents arbitrary paths from creating unbounded Prometheus label cardinality.

    Inputs:
        - path: Incoming request path.

    Outputs:
        - Known route path or the fixed label "unmatched".
    """
    return path if path in _known_routes else "unmatched"


def _safe_label(value: str | None, fallback: str = "unknown") -> str:
    """Return a bounded syntactically safe metric label or a fixed fallback."""
    if value is None or not _safe_label_pattern.fullmatch(value):
        return fallback
    return value


def observe_http_request(method: str, route: str, status_code: int, elapsed: float) -> None:
    """
    RME

    Requires:
        - elapsed is non-negative request time in seconds.

    Modifies:
        - Process-local Prometheus counters and histograms.

    Effects:
        - Records HTTP volume and latency with bounded labels only.

    Inputs:
        - method: HTTP method.
        - route: Bounded route label.
        - status_code: HTTP response status.
        - elapsed: Request duration in seconds.

    Outputs:
        - None.
    """
    method_label = method.upper() if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "OTHER"
    status_class = f"{max(1, min(5, status_code // 100))}xx"
    http_requests_total.labels(method_label, route, status_class).inc()
    http_request_duration_seconds.labels(method_label, route).observe(max(0.0, elapsed))


def observe_provider_request(provider: str, outcome: str, elapsed: float) -> None:
    """
    RME

    Requires:
        - provider and outcome contain no secrets.

    Modifies:
        - Process-local Prometheus provider metrics.

    Effects:
        - Records provider request volume and latency using bounded labels.

    Inputs:
        - provider: Provider identifier.
        - outcome: success or error.
        - elapsed: Provider latency in seconds.

    Outputs:
        - None.
    """
    provider_label = _safe_label(provider)
    outcome_label = outcome if outcome in {"success", "error"} else "other"
    provider_requests_total.labels(provider_label, outcome_label).inc()
    provider_request_duration_seconds.labels(provider_label).observe(max(0.0, elapsed))
    if outcome_label == "error":
        backend_failures_total.labels("provider").inc()


def observe_audit_event(payload: Mapping[str, Any]) -> None:
    """
    RME

    Requires:
        - payload is the already-sanitized audit payload.

    Modifies:
        - Process-local Prometheus security and usage metrics.

    Effects:
        - Derives bounded counters from existing audit decisions.
        - Never uses request IDs, client IDs, key IDs, model names, tool names, or reasons as labels.

    Inputs:
        - payload: Sanitized audit event mapping.

    Outputs:
        - None.
    """
    event = _safe_label(str(payload.get("event", "unknown")))
    outcome = _safe_label(str(payload.get("outcome", "unknown")))
    security_decisions_total.labels(event, outcome).inc()

    if event == "pii_policy":
        count = payload.get("pii_detected_count")
        types = payload.get("pii_types")
        if isinstance(count, int) and count > 0 and isinstance(types, str):
            for pii_type in set(types.split(",")):
                label = pii_type if pii_type in _known_pii_types else "other"
                pii_findings_total.labels(label).inc()

    if event == "prompt_injection":
        indicators = payload.get("prompt_injection_indicators")
        if isinstance(indicators, str):
            for indicator in set(indicators.split(",")):
                label = indicator if indicator in _known_injection_indicators else "other"
                prompt_injection_findings_total.labels(label).inc()

    stage_by_event = {
        "tool_authorization": "exposure",
        "tool_execution_ticket": "ticket",
        "tool_execution_authorization": "execution",
    }
    stage = stage_by_event.get(event)
    if stage is not None:
        risk = payload.get("tool_execution_risk")
        risk_label = risk if risk in {"read", "write", "destructive"} else "none"
        tool_authorization_decisions_total.labels(stage, outcome, risk_label).inc()

    if event == "usage_budget" and outcome == "recorded":
        provider = _safe_label(
            payload.get("provider") if isinstance(payload.get("provider"), str) else None
        )
        tokens = payload.get("request_tokens")
        cost = payload.get("request_cost_usd")
        if isinstance(tokens, int) and tokens >= 0:
            usage_tokens_total.labels(provider).inc(tokens)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            usage_cost_usd_total.labels(provider).inc(float(cost))

    if outcome == "error":
        component = {
            "rate_limit": "rate_limiter",
            "usage_budget": "usage_ledger",
            "tool_execution_ticket": "tool_execution",
            "tool_execution_authorization": "tool_execution",
        }.get(event)
        if component is not None:
            backend_failures_total.labels(component).inc()


def metrics_payload() -> bytes:
    """
    RME

    Requires:
        - Process-local metrics may have been recorded.

    Modifies:
        - Nothing.

    Effects:
        - Serializes only the dedicated Secure AI Gateway Prometheus registry.

    Inputs:
        - None.

    Outputs:
        - Prometheus text exposition bytes.
    """
    return generate_latest(metrics_registry)


@contextmanager
def request_trace(
    *,
    method: str,
    route: str,
    request_id: str,
    headers: Mapping[str, str],
) -> Iterator[Span]:
    """
    RME

    Requires:
        - route is bounded and request_id has already passed gateway validation.

    Modifies:
        - Current OpenTelemetry span context for the request scope.

    Effects:
        - Extracts an incoming W3C trace context when present.
        - Starts a server span without recording prompt/body/header contents.

    Inputs:
        - method: HTTP method.
        - route: Bounded route label.
        - request_id: Gateway request correlation ID.
        - headers: Incoming request headers used only for standard trace-context extraction.

    Outputs:
        - Active request span context manager value.
    """
    parent = propagate.extract(headers)
    with tracer.start_as_current_span(
        f"{method.upper()} {route}",
        context=parent,
        kind=SpanKind.SERVER,
        attributes={
            "http.request.method": method.upper(),
            "http.route": route,
            "sag.request_id": request_id,
        },
    ) as span:
        yield span


def finish_request_span(span: Span, status_code: int) -> str | None:
    """
    RME

    Requires:
        - span is the active request span.

    Modifies:
        - Span status and response-status attributes.

    Effects:
        - Marks 5xx responses as errors and returns a printable trace ID when sampled/valid.

    Inputs:
        - span: Active OpenTelemetry span.
        - status_code: HTTP response status code.

    Outputs:
        - Lowercase 32-character trace ID, or None for a no-op/invalid context.
    """
    span.set_attribute("http.response.status_code", status_code)
    if status_code >= 500:
        span.set_status(Status(StatusCode.ERROR))
    context = span.get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}"


@contextmanager
def provider_trace(provider: str) -> Iterator[Span]:
    """
    RME

    Requires:
        - provider is a non-secret configured provider identifier.

    Modifies:
        - Current OpenTelemetry span context during the upstream request.

    Effects:
        - Starts a child span that records provider identity but no prompts, responses, or credentials.

    Inputs:
        - provider: Provider identifier.

    Outputs:
        - Active provider span.
    """
    with tracer.start_as_current_span(
        "llm.provider.request",
        kind=SpanKind.CLIENT,
        attributes={"gen_ai.provider.name": _safe_label(provider)},
    ) as span:
        yield span


def observe_provider_operation(provider: str):
    """
    RME

    Requires:
        - provider is a configured provider identifier.

    Modifies:
        - Provider metrics and tracing state when used as a context manager.

    Effects:
        - Returns timing state used by the provider wrapper.

    Inputs:
        - provider: Provider identifier.

    Outputs:
        - Tuple of monotonic start time and provider trace context manager.
    """
    return perf_counter(), provider_trace(provider)
