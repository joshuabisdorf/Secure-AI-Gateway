import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.models import ChatCompletionRequest, ChatMessage
from app.semantic_pii import (
    SemanticPIIAnalyzer,
    redact_semantic_text,
    semantic_pii_analyzer,
)

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_email_pattern = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"(?![A-Za-z0-9_-])"
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
    """Validate per-client PII handling policy.

    Args:
        configured_policies: Serialized per-client PII policies.

    Returns:
        Mapping from client ID to PIIPolicy.

    Raises:
        ValueError: Configuration is empty, malformed, duplicated, or
            unsupported.

    Notes:
        - Precondition: configured_policies uses client_id:action records
          separated by commas.
        - Precondition: action is redact or deny.
        - Rejects malformed records, unsupported actions, and duplicate
          clients.
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
    """Fail closed when PII policy is absent, malformed, or missing the client.

    Args:
        client_id: Authenticated gateway client identity.

    Returns:
        Configured PIIPolicy for the client.

    Notes:
        - Precondition: client_id identifies an authenticated gateway client.
        - Precondition: SAG_CLIENT_PII_POLICIES may define per-client PII
          handling actions.
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
    """Applie the Luhn checksum only to 13-19 digit candidates.

    Args:
        candidate: Potential payment-card number.

    Returns:
        True when the candidate has a valid supported-length Luhn checksum.

    Notes:
        - Precondition: candidate may contain a payment-card-like digit
          sequence with separators.
    """
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


def _redact_structured_text(text: str) -> tuple[str, list[str]]:
    """Detect and redacts deterministic email, SSN, phone, and payment-card values.

    Args:
        text: Message text to inspect.

    Returns:
        Redacted text and one safe type label per finding.

    Notes:
        - Precondition: text is user/model message content.
        - Does not retain raw detected values separately.
    """
    detected_types: list[str] = []

    def redact_pattern(
        current_text: str,
        pattern: re.Pattern[str],
        pii_type: str,
        replacement: str,
    ) -> str:
        """Perform the redact pattern operation.

        Args:
            current_text: Function input.
            pattern: Function input.
            pii_type: Function input.
            replacement: Function input.

        Returns:
            A value matching the declared str return contract.

        Notes:
            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
        """
        def replace(_: re.Match[str]) -> str:
            """Perform the replace operation.

            Args:
                _: Function input.

            Returns:
                A value matching the declared str return contract.

            Notes:
                - Precondition: Arguments satisfy their declared contracts and
                  required configured dependencies are available.
            """
            detected_types.append(pii_type)
            return replacement

        return pattern.sub(replace, current_text)

    redacted = redact_pattern(text, _email_pattern, "email", "[REDACTED_EMAIL]")
    redacted = redact_pattern(redacted, _ssn_pattern, "ssn", "[REDACTED_SSN]")
    redacted = redact_pattern(
        redacted, _phone_pattern, "phone", "[REDACTED_PHONE]"
    )

    def replace_card(match: re.Match[str]) -> str:
        """Perform the replace card operation.

        Args:
            match: Function input.

        Returns:
            A value matching the declared str return contract.

        Notes:
            - Precondition: Arguments satisfy their declared contracts and
              required configured dependencies are available.
        """
        candidate = match.group(0)
        if not _luhn_valid(candidate):
            return candidate
        detected_types.append("payment_card")
        return "[REDACTED_PAYMENT_CARD]"

    redacted = _card_candidate_pattern.sub(replace_card, redacted)
    return redacted, detected_types


def inspect_and_redact_request(
    request: ChatCompletionRequest,
    semantic_analyzer: SemanticPIIAnalyzer = semantic_pii_analyzer,
) -> PIIInspectionResult:
    """Detect deterministic structured PII first.

    Args:
        request: Validated gateway chat-completion request.
        semantic_analyzer: Semantic analyzer implementation, injectable for
            tests.

    Returns:
        PIIInspectionResult with a redacted request, total finding count, and
        safe type names.

    Notes:
        - Precondition: request is a validated chat-completion request.
        - Precondition: semantic_analyzer is a configured local semantic PII
          analyzer.
        - Side effect: Lazy semantic model state on first semantic inspection.
        - Side effect: Nothing in the original request object.
        - Runs local semantic/contextual PII detection on the
          structured-redacted text.
        - Redacts all selected values in a copied provider request.
        - Leaves non-text/tool-call message fields unchanged.
        - Does not retain or return raw detected values separately.
    """
    detected_types: list[str] = []
    redacted_messages: list[ChatMessage] = []

    for message in request.messages:
        if message.content is None:
            redacted_messages.append(message)
            continue

        structured_redacted, structured_types = _redact_structured_text(
            message.content
        )
        semantic_redacted, semantic_types = redact_semantic_text(
            structured_redacted,
            semantic_analyzer,
        )
        detected_types.extend(structured_types)
        detected_types.extend(semantic_types)
        redacted_messages.append(
            message.model_copy(update={"content": semantic_redacted})
        )

    return PIIInspectionResult(
        redacted_request=request.model_copy(
            update={"messages": redacted_messages}
        ),
        detected_count=len(detected_types),
        detected_types=tuple(sorted(set(detected_types))),
    )
