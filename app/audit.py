"""Emit sanitized security audit events."""

import json
import logging
from datetime import datetime, timezone

from app.observability import observe_audit_event

audit_logger = logging.getLogger("secure_ai_gateway.audit")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False

if not audit_logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(console_handler)


def emit_audit_event(
    *,
    request_id: str,
    event: str,
    outcome: str,
    client_id: str | None = None,
    key_id: str | None = None,
    requested_model: str | None = None,
    resolved_model: str | None = None,
    provider: str | None = None,
    reason: str | None = None,
    latency_ms: float | None = None,
    limit_rpm: int | None = None,
    remaining: int | None = None,
    retry_after_seconds: int | None = None,
    request_tokens: int | None = None,
    request_cost_usd: float | None = None,
    token_limit_daily: int | None = None,
    tokens_used_daily: int | None = None,
    tokens_remaining_daily: int | None = None,
    cost_limit_daily_usd: float | None = None,
    cost_used_daily_usd: float | None = None,
    cost_remaining_daily_usd: float | None = None,
    budget_reset_at: str | None = None,
    pii_detected_count: int | None = None,
    pii_types: str | None = None,
    prompt_injection_detected_count: int | None = None,
    prompt_injection_score: int | None = None,
    prompt_injection_indicators: str | None = None,
    tool_requested_count: int | None = None,
    tool_requested_names: str | None = None,
    tool_denied_names: str | None = None,
    tool_call_id: str | None = None,
    tool_execution_id: str | None = None,
    tool_execution_risk: str | None = None,
    source_request_id: str | None = None,
) -> None:
    """Emit one JSON audit record to application stderr.

    :param request_id: Correlation identifier for the request.
    :param event: Audit event category.
    :param outcome: Decision or result for the event.
    :param client_id: Optional authenticated gateway client identity.
    :param key_id: Optional public identifier for the authenticated gateway
        key.
    :param requested_model: Optional model name requested by the gateway
        client.
    :param resolved_model: Optional model name reported by the upstream
        provider.
    :param provider: Optional upstream provider name.
    :param reason: Optional non-secret decision reason.
    :param latency_ms: Optional elapsed request latency in milliseconds.
    :param limit_rpm: Optional requests-per-minute limit applied to the client.
    :param remaining: Optional requests remaining in the current rate-limit
        window.
    :param retry_after_seconds: Optional delay before a denied client should
        retry.
    :param request_tokens: Optional provider-reported tokens used by one
        request.
    :param request_cost_usd: Optional provider-reported USD cost for one
        request.
    :param token_limit_daily: Optional configured daily token budget.
    :param tokens_used_daily: Optional cumulative UTC-day token usage.
    :param tokens_remaining_daily: Optional remaining UTC-day token capacity.
    :param cost_limit_daily_usd: Optional configured daily USD budget.
    :param cost_used_daily_usd: Optional cumulative UTC-day USD cost.
    :param cost_remaining_daily_usd: Optional remaining UTC-day USD capacity.
    :param budget_reset_at: Optional ISO timestamp for the next budget reset.
    :param pii_detected_count: Optional count of PII findings without raw
        values.
    :param pii_types: Optional comma-separated PII type names without raw
        values.
    :param prompt_injection_detected_count: Optional count of unique injection
        indicators.
    :param prompt_injection_score: Optional aggregate deterministic injection
        score.
    :param prompt_injection_indicators: Optional comma-separated safe indicator
        labels.
    :param tool_requested_count: Optional number of distinct function tools
        exposed.
    :param tool_requested_names: Optional comma-separated validated function
        names.
    :param tool_denied_names: Optional comma-separated function names denied by
        policy.
    :param tool_call_id: Optional provider-issued tool-call identifier.
    :param tool_execution_id: Optional gateway-issued one-time execution
        identifier.
    :param tool_execution_risk: Optional read/write/destructive risk
        classification.
    :param source_request_id: Optional chat request that produced the tool
        call.

    .. note::

        - Precondition: request_id identifies the gateway request being
          audited.
        - Precondition: event and outcome describe a security-relevant action
          or decision.
        - Side effect: The process logging stream through the audit logger.
        - Side effect: Best-effort bounded Prometheus counters derived from
          sanitized metadata.
        - Derives metrics only from already-sanitized audit metadata.
        - Observability failures do not interrupt authorization or audit
          logging.
        - Omits prompt content, detected PII values, credentials, execution
          tickets, tool arguments, and tool outputs from the audit record.
        - Records prompt-injection and tool-authorization metadata only as safe
          labels/names/IDs.
    """
    payload: dict[str, str | float | int] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "event": event,
        "outcome": outcome,
    }

    optional_fields = {
        "client_id": client_id,
        "key_id": key_id,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "provider": provider,
        "reason": reason,
        "limit_rpm": limit_rpm,
        "remaining": remaining,
        "retry_after_seconds": retry_after_seconds,
        "request_tokens": request_tokens,
        "request_cost_usd": request_cost_usd,
        "token_limit_daily": token_limit_daily,
        "tokens_used_daily": tokens_used_daily,
        "tokens_remaining_daily": tokens_remaining_daily,
        "cost_limit_daily_usd": cost_limit_daily_usd,
        "cost_used_daily_usd": cost_used_daily_usd,
        "cost_remaining_daily_usd": cost_remaining_daily_usd,
        "budget_reset_at": budget_reset_at,
        "pii_detected_count": pii_detected_count,
        "pii_types": pii_types,
        "prompt_injection_detected_count": prompt_injection_detected_count,
        "prompt_injection_score": prompt_injection_score,
        "prompt_injection_indicators": prompt_injection_indicators,
        "tool_requested_count": tool_requested_count,
        "tool_requested_names": tool_requested_names,
        "tool_denied_names": tool_denied_names,
        "tool_call_id": tool_call_id,
        "tool_execution_id": tool_execution_id,
        "tool_execution_risk": tool_execution_risk,
        "source_request_id": source_request_id,
    }
    for field, value in optional_fields.items():
        if value is not None:
            payload[field] = value

    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 3)

    try:
        observe_audit_event(payload)
    except Exception:
        # Telemetry is never part of an allow/deny decision. Audit logging
        # remains authoritative.
        pass

    audit_logger.info(
        json.dumps(payload, separators=(",", ":"), sort_keys=True)
    )
