import base64
import binascii
import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.models import ChatCompletionRequest

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_supported_actions = frozenset({"audit", "deny", "off"})
_base64_candidate_pattern = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
_hex_candidate_pattern = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){16,}(?![0-9A-Fa-f])")
_max_encoded_candidates = 32
_max_encoded_candidate_chars = 4096


@dataclass(frozen=True)
class PromptInjectionPolicy:
    action: str


@dataclass(frozen=True)
class PromptInjectionResult:
    detected_count: int
    score: int
    indicators: tuple[str, ...]


@dataclass(frozen=True)
class _Indicator:
    name: str
    weight: int
    patterns: tuple[re.Pattern[str], ...]


_indicators = (
    _Indicator(
        name="instruction_override",
        weight=4,
        patterns=(
            re.compile(
                r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?"
                r"(?:previous|prior|above|earlier|system|developer)\s+"
                r"(?:instructions?|rules?|directions?|messages?)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:ignore|disregard)\s+(?:the\s+)?(?:system|developer)\s+"
                r"(?:prompt|message|instructions?)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Indicator(
        name="system_prompt_extraction",
        weight=4,
        patterns=(
            re.compile(
                r"\b(?:reveal|show|print|display|repeat|output|provide)\b.{0,48}"
                r"\b(?:system|developer|hidden|initial)\s+"
                r"(?:prompt|message|instructions?)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\bwhat\s+(?:were|are)\s+(?:your\s+)?(?:exact\s+)?"
                r"(?:system\s+|developer\s+|hidden\s+|initial\s+)?instructions\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Indicator(
        name="role_impersonation",
        weight=3,
        patterns=(
            re.compile(
                r"\byou\s+are\s+now\s+(?:in\s+)?"
                r"(?:developer|admin|administrator|root|debug|unrestricted)\s+mode\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bact\s+as\s+(?:an?\s+)?"
                r"(?:unrestricted|uncensored|developer|system|administrator|root)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Indicator(
        name="safety_bypass",
        weight=3,
        patterns=(
            re.compile(
                r"\b(?:bypass|disable|circumvent|override|ignore)\b.{0,48}"
                r"\b(?:safety|security|policy|policies|guardrails?|restrictions?|filters?)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    ),
    _Indicator(
        name="secret_exfiltration",
        weight=4,
        patterns=(
            re.compile(
                r"\b(?:reveal|show|print|display|output|provide|give\s+me)\b.{0,64}"
                r"\b(?:api[ _-]?keys?|passwords?|secrets?|credentials?|tokens?)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    ),
)
_indicator_weights = {indicator.name: indicator.weight for indicator in _indicators}
_indicator_weights["encoded_payload"] = 5


def parse_client_prompt_injection_policies(
    configured_policies: str,
) -> dict[str, PromptInjectionPolicy]:
    """
    RME

    Requires:
        - configured_policies uses client_id:action records separated by commas.
        - action is audit, deny, or off.

    Modifies:
        - Nothing.

    Effects:
        - Validates per-client prompt-injection enforcement configuration.
        - Rejects malformed records, duplicate clients, and unsupported actions.

    Inputs:
        - configured_policies: Serialized prompt-injection policy records.

    Outputs:
        - Mapping from client ID to validated PromptInjectionPolicy.

    Raises:
        - ValueError: Configuration is empty, malformed, duplicated, or unsupported.
    """
    policies: dict[str, PromptInjectionPolicy] = {}

    for raw_record in configured_policies.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":", 1)
        if len(parts) != 2:
            raise ValueError("invalid_prompt_injection_policy_record")

        client_id, action = (part.strip() for part in parts)
        action = action.lower()
        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if action not in _supported_actions:
            raise ValueError("invalid_prompt_injection_action")
        if client_id in policies:
            raise ValueError("duplicate_client_prompt_injection_policy")

        policies[client_id] = PromptInjectionPolicy(action=action)

    if not policies:
        raise ValueError("no_client_prompt_injection_policies")

    return policies


def get_client_prompt_injection_policy(client_id: str) -> PromptInjectionPolicy:
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_CLIENT_PROMPT_INJECTION_POLICIES may define per-client actions.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed when prompt-injection policy is absent, malformed, or missing the client.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Prompt-injection policy configured for the client.
    """
    configured_policies = os.getenv("SAG_CLIENT_PROMPT_INJECTION_POLICIES")
    if not configured_policies:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt injection policy is not configured.",
        )

    try:
        policies = parse_client_prompt_injection_policies(configured_policies)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt injection policy is not configured.",
        ) from None

    policy = policies.get(client_id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt injection policy is not configured for this client.",
        )

    return policy


def _match_direct_indicators(text: str) -> set[str]:
    matched: set[str] = set()
    for indicator in _indicators:
        if any(pattern.search(text) for pattern in indicator.patterns):
            matched.add(indicator.name)
    return matched


def _decode_base64(candidate: str) -> str | None:
    if len(candidate) > _max_encoded_candidate_chars:
        return None

    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True)
        return decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _decode_hex(candidate: str) -> str | None:
    if len(candidate) > _max_encoded_candidate_chars:
        return None

    try:
        return bytes.fromhex(candidate).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _match_encoded_indicators(text: str) -> set[str]:
    matched: set[str] = set()
    processed = 0

    for pattern, decoder in (
        (_base64_candidate_pattern, _decode_base64),
        (_hex_candidate_pattern, _decode_hex),
    ):
        for match in pattern.finditer(text):
            if processed >= _max_encoded_candidates:
                return matched
            processed += 1

            decoded = decoder(match.group(0))
            if decoded is None:
                continue

            decoded_matches = _match_direct_indicators(decoded)
            if decoded_matches:
                matched.update(decoded_matches)
                matched.add("encoded_payload")

    return matched


def inspect_prompt_injection(request: ChatCompletionRequest) -> PromptInjectionResult:
    """
    RME

    Requires:
        - request is a validated chat-completion request.

    Modifies:
        - Nothing.

    Effects:
        - Inspects all client-supplied message content for explicit prompt-injection indicators.
        - Decodes bounded Base64/hex candidates only to inspect them for the same indicators.
        - Returns only indicator labels and scores; raw prompt fragments are never retained.

    Inputs:
        - request: Chat-completion request to inspect before provider forwarding.

    Outputs:
        - PromptInjectionResult with unique indicator count, aggregate score, and labels.
    """
    matched: set[str] = set()

    for message in request.messages:
        matched.update(_match_direct_indicators(message.content))
        matched.update(_match_encoded_indicators(message.content))

    ordered = tuple(name for name in _indicator_weights if name in matched)
    score = sum(_indicator_weights[name] for name in ordered)
    return PromptInjectionResult(
        detected_count=len(ordered),
        score=score,
        indicators=ordered,
    )
