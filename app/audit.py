import json
import logging
from datetime import datetime, timezone


audit_logger = logging.getLogger("secure_ai_gateway.audit")
audit_logger.setLevel(logging.INFO)


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
) -> None:
    """
    RME

    Requires:
        - request_id identifies the gateway request being audited.
        - event and outcome describe a security-relevant action or decision.

    Modifies:
        - The process logging stream through the audit logger.

    Effects:
        - Emits one JSON audit record without prompt content or credentials.

    Inputs:
        - request_id: Correlation identifier for the request.
        - event: Audit event category.
        - outcome: Decision or result for the event.
        - client_id: Optional authenticated gateway client identity.
        - key_id: Optional public identifier for the authenticated gateway key.
        - requested_model: Optional model name requested by the gateway client.
        - resolved_model: Optional model name reported by the upstream provider.
        - provider: Optional upstream provider name.
        - reason: Optional non-secret decision reason.
        - latency_ms: Optional elapsed request latency in milliseconds.

    Outputs:
        - None.
    """
    payload: dict[str, str | float] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "event": event,
        "outcome": outcome,
    }

    if client_id is not None:
        payload["client_id"] = client_id
    if key_id is not None:
        payload["key_id"] = key_id
    if requested_model is not None:
        payload["requested_model"] = requested_model
    if resolved_model is not None:
        payload["resolved_model"] = resolved_model
    if provider is not None:
        payload["provider"] = provider
    if reason is not None:
        payload["reason"] = reason
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 3)

    audit_logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
