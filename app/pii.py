import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.models import ChatCompletionRequest, ChatMessage

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_email_pattern = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"(?![A-Za-z0-9._-])"
)
_ssn_pattern = re.compile(
    r"(?<!\d)(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)"
)
_phone_pattern = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)\s*|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\d)"
)
_card_candidate_pattern = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
_valid_actions = frozenset({"redact", "deny"})


@dataclass(frozen=True)
class PIIPolicy:
    action: str


@dataclass(frozen=True)
class PIIInspectionResult:
    redacted_request: ChatCompletionRequest
    detected_count: int
    detected_types: tuple[str, ...]


def parse_client_pii_policies(configured_policies: str) -> dict[str, PIIPolicy]:
    """
    RME

    Requires:
        - configured_policies uses client_id:action records separated by commas.
        - action is redact or deny.

    Modifies:
        - Nothing.

    Effects:
        - Validates per-client PII handling policy.
        - Rejects malformed records, unsupported actions, and duplicate clients.

    Inputs:
        - configured_policies: Serialized per-client PII policies.

    Outputs:
        - Mapping from client ID to PIIPolicy.

    Raises:
        - ValueError: Configuration is empty, malformed, duplicated, or unsupported.
    """
    policies: dict[str, PIIPolicy] = {}

    for raw_record in configured_policies.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":", 1)
        if len(parts) != 2:
            raise ValueError("invalid_pii_policy_record")

        client_id, action = (part.strip() for part in parts)
        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if action not in _valid_actions:
            raise ValueError("invalid_pii_action")
        if client_id in policies:
            raise ValueError("duplicate_client_pii_policy")

        policies[client_id] = PIIPolicy(action=action)

    if not policies:
        raise ValueError("no_client_pii_policies")

    return policies


def get_client_pii_policy(client_id: str) -> PIIPolicy:
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_CLIENT_PII_POLICIES may define per-client PII handling actions.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed when PII policy is absent, malformed, or missing the client.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Configured PIIPolicy for the client.
    """
    configured_policies = os.getenv("SAG_CLIENT_PII_POLICIES")
    if not configured_policies:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PII policy is not configured.",
        )

    try:
        policies = parse_client_pii_policies(configured_policies)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PII policy is not configured.",
        ) from None

    policy = policies.get(client_id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PII policy is not configured for this client.",
        )

    return policy


def _luhn_valid(candidate: str) -> bool:
    digits = [int(character) for character in candidate if character.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False

    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        value = digit
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value

    return checksum % 10 == 0


def _redact_text(text: str) -> tuple[str, list[str]]:
    detected_types: list[str] = []

    def redact_pattern(
        current_text: str,
        pattern: re.Pattern[str],
        pii_type: str,
        replacement: str,
    ) -> str:
        def replace(_: re.Match[str]) -> str:
            detected_types.append(pii_type)
            return replacement

        return pattern.sub(replace, current_text)

    redacted = redact_pattern(text, _email_pattern, "email", "[REDACTED_EMAIL]")
    redacted = redact_pattern(redacted, _ssn_pattern, "ssn", "[REDACTED_SSN]")
    redacted = redact_pattern(redacted, _phone_pattern, "phone", "[REDACTED_PHONE]")

    def replace_card(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if not _luhn_valid(candidate):
            return candidate
        detected_types.append("payment_card")
        return "[REDACTED_PAYMENT_CARD]"

    redacted = _card_candidate_pattern.sub(replace_card, redacted)
    return redacted, detected_types


def inspect_and_redact_request(request: ChatCompletionRequest) -> PIIInspectionResult:
    """
    RME

    Requires:
        - request is a validated chat-completion request containing string message content.

    Modifies:
        - Nothing. The original request object is not changed.

    Effects:
        - Detects common structured PII in message content.
        - Redacts detected values in a copied request.
        - Does not retain or return raw detected values separately.

    Inputs:
        - request: Validated gateway chat-completion request.

    Outputs:
        - PIIInspectionResult with a redacted request, total finding count, and safe type names.
    """
    detected_types: list[str] = []
    redacted_messages: list[ChatMessage] = []

    for message in request.messages:
        redacted_content, message_types = _redact_text(message.content)
        detected_types.extend(message_types)
        redacted_messages.append(message.model_copy(update={"content": redacted_content}))

    return PIIInspectionResult(
        redacted_request=request.model_copy(update={"messages": redacted_messages}),
        detected_count=len(detected_types),
        detected_types=tuple(sorted(set(detected_types))),
    )
