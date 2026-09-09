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
    model: str | None = None,
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
        - model: Optional requested model name.
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

    if model is not None:
        payload["model"] = model
    if reason is not None:
        payload["reason"] = reason
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 3)

    audit_logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
