import argparse
import json
import os
import re
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_profile_name_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_model_name_pattern = re.compile(r"^[^\s,]{1,256}$")
_tool_name_pattern = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_max_rate_limit_rpm = 1_000_000
_max_daily_tokens = 1_000_000_000
_max_daily_cost_usd = Decimal("1000000")
_max_policy_file_bytes = 1_048_576
_supported_pii_actions = frozenset({"redact", "deny"})
_supported_prompt_injection_actions = frozenset({"audit", "deny", "off"})
_profile_keys = frozenset(
    {
        "allowed_models",
        "requests_per_minute",
        "daily_budget",
        "pii_action",
        "prompt_injection_action",
        "allowed_tools",
    }
)
_daily_budget_keys = frozenset({"tokens", "cost_usd"})
_root_keys = frozenset({"version", "profiles", "clients"})

_cache_lock = threading.Lock()
_cache_signature: tuple[str, int, int] | None = None
_cache_registry: "SecurityPolicyRegistry | None" = None


class SecurityPolicyUnavailable(RuntimeError):
    """Raised when the configured unified security policy cannot be used safely."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class DailyBudgetPolicy:
    token_limit_daily: int | None
    cost_limit_daily_usd: Decimal | None


@dataclass(frozen=True)
class SecurityPolicyProfile:
    allowed_models: frozenset[str]
    requests_per_minute: int
    daily_budget: DailyBudgetPolicy
    pii_action: str
    prompt_injection_action: str
    allowed_tools: frozenset[str]


@dataclass(frozen=True)
class ResolvedSecurityPolicy:
    client_id: str
    profile_name: str
    profile: SecurityPolicyProfile


@dataclass(frozen=True)
class SecurityPolicyRegistry:
    version: int
    profiles: dict[str, SecurityPolicyProfile]
    clients: dict[str, str]

    def resolve(self, client_id: str) -> ResolvedSecurityPolicy:
        """
        RME

        Requires:
            - client_id identifies an authenticated gateway client.
            - The registry has already passed schema validation.

        Modifies:
            - Nothing.

        Effects:
            - Resolves a client assignment to its named reusable security profile.
            - Fails closed when the client has no assignment.

        Inputs:
            - client_id: Authenticated gateway client identity.

        Outputs:
            - ResolvedSecurityPolicy containing the assigned profile and its name.
        """
        profile_name = self.clients.get(client_id)
        if profile_name is None:
            raise SecurityPolicyUnavailable("client_security_policy_not_configured")
        return ResolvedSecurityPolicy(
            client_id=client_id,
            profile_name=profile_name,
            profile=self.profiles[profile_name],
        )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate_json_key")
        parsed[key] = value
    return parsed


def _require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
    reason: str,
) -> None:
    if frozenset(value) != expected:
        raise ValueError(reason)


def _parse_string_list(
    value: Any,
    *,
    pattern: re.Pattern[str],
    allow_empty: bool,
    invalid_reason: str,
) -> frozenset[str]:
    if not isinstance(value, list):
        raise ValueError(invalid_reason)
    if not value and not allow_empty:
        raise ValueError(invalid_reason)

    parsed: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise ValueError(invalid_reason)
        if item in parsed:
            raise ValueError(invalid_reason)
        parsed.add(item)
    return frozenset(parsed)


def _parse_daily_budget(value: Any) -> DailyBudgetPolicy:
    if not isinstance(value, dict):
        raise ValueError("invalid_daily_budget")
    _require_exact_keys(value, _daily_budget_keys, "invalid_daily_budget")

    raw_tokens = value["tokens"]
    token_limit: int | None
    if raw_tokens is None:
        token_limit = None
    elif (
        isinstance(raw_tokens, int)
        and not isinstance(raw_tokens, bool)
        and 1 <= raw_tokens <= _max_daily_tokens
    ):
        token_limit = raw_tokens
    else:
        raise ValueError("invalid_token_budget")

    raw_cost = value["cost_usd"]
    cost_limit: Decimal | None
    if raw_cost is None:
        cost_limit = None
    elif isinstance(raw_cost, str):
        try:
            cost_limit = Decimal(raw_cost)
        except InvalidOperation:
            raise ValueError("invalid_cost_budget") from None
        if (
            not cost_limit.is_finite()
            or cost_limit <= 0
            or cost_limit > _max_daily_cost_usd
        ):
            raise ValueError("invalid_cost_budget")
    else:
        raise ValueError("invalid_cost_budget")

    if token_limit is None and cost_limit is None:
        raise ValueError("empty_daily_budget")

    return DailyBudgetPolicy(
        token_limit_daily=token_limit,
        cost_limit_daily_usd=cost_limit,
    )


def _parse_profile(value: Any) -> SecurityPolicyProfile:
    if not isinstance(value, dict):
        raise ValueError("invalid_security_profile")
    _require_exact_keys(value, _profile_keys, "invalid_security_profile")

    allowed_models = _parse_string_list(
        value["allowed_models"],
        pattern=_model_name_pattern,
        allow_empty=False,
        invalid_reason="invalid_profile_models",
    )

    rate_limit = value["requests_per_minute"]
    if not (
        isinstance(rate_limit, int)
        and not isinstance(rate_limit, bool)
        and 1 <= rate_limit <= _max_rate_limit_rpm
    ):
        raise ValueError("invalid_profile_rate_limit")

    pii_action = value["pii_action"]
    if not isinstance(pii_action, str) or pii_action not in _supported_pii_actions:
        raise ValueError("invalid_profile_pii_action")

    prompt_injection_action = value["prompt_injection_action"]
    if (
        not isinstance(prompt_injection_action, str)
        or prompt_injection_action not in _supported_prompt_injection_actions
    ):
        raise ValueError("invalid_profile_prompt_injection_action")

    allowed_tools = _parse_string_list(
        value["allowed_tools"],
        pattern=_tool_name_pattern,
        allow_empty=True,
        invalid_reason="invalid_profile_tools",
    )

    return SecurityPolicyProfile(
        allowed_models=allowed_models,
        requests_per_minute=rate_limit,
        daily_budget=_parse_daily_budget(value["daily_budget"]),
        pii_action=pii_action,
        prompt_injection_action=prompt_injection_action,
        allowed_tools=allowed_tools,
    )


def parse_security_policy_registry(document: str) -> SecurityPolicyRegistry:
    """
    RME

    Requires:
        - document is intended to be a version-1 Secure AI Gateway policy registry.

    Modifies:
        - Nothing.

    Effects:
        - Strictly validates JSON structure, duplicate keys, profile fields, and assignments.
        - Rejects unknown fields rather than silently ignoring policy mistakes.

    Inputs:
        - document: UTF-8 JSON policy document text.

    Outputs:
        - Validated SecurityPolicyRegistry.

    Raises:
        - ValueError: The policy document is malformed or violates the schema.
    """
    try:
        root = json.loads(document, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_security_policy_json") from exc

    if not isinstance(root, dict):
        raise ValueError("invalid_security_policy_root")
    _require_exact_keys(root, _root_keys, "invalid_security_policy_root")

    version = root["version"]
    if version != 1 or isinstance(version, bool):
        raise ValueError("unsupported_security_policy_version")

    raw_profiles = root["profiles"]
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError("no_security_profiles")

    profiles: dict[str, SecurityPolicyProfile] = {}
    for profile_name, raw_profile in raw_profiles.items():
        if (
            not isinstance(profile_name, str)
            or not _profile_name_pattern.fullmatch(profile_name)
        ):
            raise ValueError("invalid_profile_name")
        profiles[profile_name] = _parse_profile(raw_profile)

    raw_clients = root["clients"]
    if not isinstance(raw_clients, dict) or not raw_clients:
        raise ValueError("no_client_policy_assignments")

    clients: dict[str, str] = {}
    for client_id, profile_name in raw_clients.items():
        if not isinstance(client_id, str) or not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if not isinstance(profile_name, str) or profile_name not in profiles:
            raise ValueError("unknown_security_profile")
        clients[client_id] = profile_name

    return SecurityPolicyRegistry(
        version=version,
        profiles=profiles,
        clients=clients,
    )


def clear_security_policy_cache() -> None:
    """Clear the process-local parsed policy cache used by tests and file reloads."""
    global _cache_signature, _cache_registry
    with _cache_lock:
        _cache_signature = None
        _cache_registry = None


def load_security_policy_registry(
    configured_path: str | None = None,
) -> SecurityPolicyRegistry:
    """
    RME

    Requires:
        - configured_path or SAG_SECURITY_POLICY_FILE identifies a local JSON policy file.

    Modifies:
        - Process-local validated-policy cache.

    Effects:
        - Reads and validates the configured non-secret policy registry.
        - Reloads automatically when file path, size, or nanosecond mtime changes.
        - Fails closed for absent, unreadable, oversized, or invalid policy files.

    Inputs:
        - configured_path: Optional explicit file path, primarily for validation/tests.

    Outputs:
        - Validated SecurityPolicyRegistry.
    """
    raw_path = configured_path or os.getenv("SAG_SECURITY_POLICY_FILE")
    if not raw_path or not raw_path.strip():
        raise SecurityPolicyUnavailable("security_policy_file_not_configured")

    path = Path(raw_path.strip())
    try:
        stat = path.stat()
        resolved_path = str(path.resolve())
    except OSError as exc:
        raise SecurityPolicyUnavailable("security_policy_file_unavailable") from exc

    if not path.is_file() or stat.st_size > _max_policy_file_bytes:
        raise SecurityPolicyUnavailable("security_policy_file_unavailable")

    signature = (resolved_path, stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        if _cache_signature == signature and _cache_registry is not None:
            return _cache_registry

    try:
        document = path.read_text(encoding="utf-8")
        registry = parse_security_policy_registry(document)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SecurityPolicyUnavailable("invalid_security_policy_file") from exc

    global _cache_signature, _cache_registry
    with _cache_lock:
        _cache_signature = signature
        _cache_registry = registry
    return registry


def resolve_client_security_policy(client_id: str) -> ResolvedSecurityPolicy | None:
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_SECURITY_POLICY_FILE may enable the unified policy registry.

    Modifies:
        - Validated-policy cache when the configured file changes.

    Effects:
        - Returns None only when unified policy configuration is not enabled, allowing
          temporary migration compatibility with legacy per-control environment variables.
        - Once SAG_SECURITY_POLICY_FILE is set, any file/client error fails closed and
          never falls back to legacy environment policy.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Resolved unified policy, or None when unified policy is not configured.
    """
    configured_path = os.getenv("SAG_SECURITY_POLICY_FILE")
    if not configured_path or not configured_path.strip():
        return None

    registry = load_security_policy_registry(configured_path)
    return registry.resolve(client_id)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the configured Secure AI Gateway security policy registry."
    )
    parser.add_argument(
        "command",
        choices=("validate",),
        help="Policy operation to perform.",
    )
    return parser


def main() -> None:
    """
    RME

    Requires:
        - SAG_SECURITY_POLICY_FILE is configured for the validate command.

    Modifies:
        - Terminal output.

    Effects:
        - Validates the complete policy registry without printing policy contents.
        - Exits nonzero when configuration cannot be used safely.

    Inputs:
        - Command-line arguments and SAG_SECURITY_POLICY_FILE.

    Outputs:
        - A concise validation summary containing only version/count metadata.
    """
    args = _build_parser().parse_args()
    if args.command != "validate":
        raise SystemExit(2)

    try:
        registry = load_security_policy_registry()
    except SecurityPolicyUnavailable as exc:
        print(f"ERROR security_policy reason={exc.reason}")
        raise SystemExit(2) from None

    print(
        "VALID security_policy "
        f"version={registry.version} "
        f"profiles={len(registry.profiles)} clients={len(registry.clients)}"
    )


if __name__ == "__main__":
    main()
