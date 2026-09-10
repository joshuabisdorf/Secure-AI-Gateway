import os

from app.security_policy import SecurityPolicyRegistry, SecurityPolicyUnavailable, load_security_policy_registry

_legacy_policy_variables = (
    "SAG_CLIENT_ALLOWED_MODELS",
    "SAG_CLIENT_RATE_LIMITS",
    "SAG_CLIENT_DAILY_BUDGETS",
    "SAG_CLIENT_PII_POLICIES",
    "SAG_CLIENT_PROMPT_INJECTION_POLICIES",
    "SAG_CLIENT_ALLOWED_TOOLS",
)


def _compile_policy_environment(registry: SecurityPolicyRegistry) -> dict[str, str]:
    """
    RME

    Requires:
        - registry has passed unified security-policy validation.

    Modifies:
        - Nothing.

    Effects:
        - Converts named profiles/client assignments into the existing internal policy formats.
        - Preserves an empty tool allowlist as the explicit client:- no-tool record.

    Inputs:
        - registry: Validated unified policy registry.

    Outputs:
        - Environment-variable values consumed by existing enforcement modules.
    """
    model_records: list[str] = []
    rate_records: list[str] = []
    budget_records: list[str] = []
    pii_records: list[str] = []
    injection_records: list[str] = []
    tool_records: list[str] = []

    for client_id in sorted(registry.clients):
        resolved = registry.resolve(client_id)
        profile = resolved.profile

        for model in sorted(profile.allowed_models):
            model_records.append(f"{client_id}:{model}")

        rate_records.append(f"{client_id}:{profile.requests_per_minute}")

        token_limit = (
            "-"
            if profile.daily_budget.token_limit_daily is None
            else str(profile.daily_budget.token_limit_daily)
        )
        cost_limit = (
            "-"
            if profile.daily_budget.cost_limit_daily_usd is None
            else str(profile.daily_budget.cost_limit_daily_usd)
        )
        budget_records.append(f"{client_id}:{token_limit}:{cost_limit}")

        pii_records.append(f"{client_id}:{profile.pii_action}")
        injection_records.append(
            f"{client_id}:{profile.prompt_injection_action}"
        )

        if profile.allowed_tools:
            for tool_name in sorted(profile.allowed_tools):
                tool_records.append(f"{client_id}:{tool_name}")
        else:
            tool_records.append(f"{client_id}:-")

    return {
        "SAG_CLIENT_ALLOWED_MODELS": ",".join(model_records),
        "SAG_CLIENT_RATE_LIMITS": ",".join(rate_records),
        "SAG_CLIENT_DAILY_BUDGETS": ",".join(budget_records),
        "SAG_CLIENT_PII_POLICIES": ",".join(pii_records),
        "SAG_CLIENT_PROMPT_INJECTION_POLICIES": ",".join(injection_records),
        "SAG_CLIENT_ALLOWED_TOOLS": ",".join(tool_records),
    }


def apply_unified_security_policy_environment() -> None:
    """
    RME

    Requires:
        - SAG_SECURITY_POLICY_FILE may identify a unified JSON security-policy registry.

    Modifies:
        - Process environment values used by per-client enforcement modules.

    Effects:
        - Does nothing when the unified registry is not enabled, preserving temporary legacy compatibility.
        - Once enabled, clears legacy per-control inputs before validating the unified registry.
        - Compiles a valid registry into internal policy inputs exactly once at process startup.
        - Leaves controls unconfigured and records a safe reason when unified policy loading fails.

    Inputs:
        - None.

    Outputs:
        - None.
    """
    configured_path = os.getenv("SAG_SECURITY_POLICY_FILE")
    if not configured_path or not configured_path.strip():
        return

    for variable in _legacy_policy_variables:
        os.environ.pop(variable, None)
    os.environ.pop("SAG_SECURITY_POLICY_ACTIVE", None)
    os.environ.pop("SAG_SECURITY_POLICY_ERROR", None)

    try:
        registry = load_security_policy_registry(configured_path)
    except SecurityPolicyUnavailable as exc:
        os.environ["SAG_SECURITY_POLICY_ERROR"] = exc.reason
        return

    compiled = _compile_policy_environment(registry)
    os.environ.update(compiled)
    os.environ["SAG_SECURITY_POLICY_ACTIVE"] = "1"
