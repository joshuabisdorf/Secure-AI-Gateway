import json
import os
from decimal import Decimal

import pytest

from app.pii import get_client_pii_policy
from app.policies.model_access import get_client_allowed_models
from app.prompt_injection import get_client_prompt_injection_policy
from app.rate_limit import get_client_rate_limit
from app.security_policy import clear_security_policy_cache, parse_security_policy_registry
from app.security_policy_bootstrap import apply_unified_security_policy_environment
from app.tool_authorization import get_client_allowed_tools
from app.usage_budget import get_client_usage_budget


def _policy_document() -> dict[str, object]:
    return {
        "version": 1,
        "profiles": {
            "baseline": {
                "allowed_models": ["mock-model"],
                "requests_per_minute": 37,
                "daily_budget": {
                    "tokens": 1234,
                    "cost_usd": "2.50",
                },
                "pii_action": "deny",
                "prompt_injection_action": "off",
                "allowed_tools": ["calculator", "lookup"],
            },
            "locked-down": {
                "allowed_models": ["mock-model"],
                "requests_per_minute": 5,
                "daily_budget": {
                    "tokens": 100,
                    "cost_usd": None,
                },
                "pii_action": "deny",
                "prompt_injection_action": "deny",
                "allowed_tools": [],
            },
        },
        "clients": {
            "test-client": "baseline",
            "service-a": "locked-down",
        },
    }


def _track_policy_environment(monkeypatch) -> None:
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_MODELS", "legacy-client:legacy-model")
    monkeypatch.setenv("SAG_CLIENT_RATE_LIMITS", "legacy-client:1")
    monkeypatch.setenv("SAG_CLIENT_DAILY_BUDGETS", "legacy-client:1:1.00")
    monkeypatch.setenv("SAG_CLIENT_PII_POLICIES", "legacy-client:redact")
    monkeypatch.setenv("SAG_CLIENT_PROMPT_INJECTION_POLICIES", "legacy-client:audit")
    monkeypatch.setenv("SAG_CLIENT_ALLOWED_TOOLS", "legacy-client:-")
    monkeypatch.delenv("SAG_SECURITY_POLICY_ACTIVE", raising=False)
    monkeypatch.delenv("SAG_SECURITY_POLICY_ERROR", raising=False)


def test_parse_security_policy_registry_resolves_reusable_profiles() -> None:
    """
    RME

    Requires:
        - A version-1 policy document contains valid reusable profiles and assignments.

    Modifies:
        - Nothing.

    Effects:
        - Verifies clients resolve to complete validated security profiles.
        - Verifies decimal cost limits are preserved exactly.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether profile parsing/resolution works.
    """
    registry = parse_security_policy_registry(json.dumps(_policy_document()))

    resolved = registry.resolve("test-client")
    assert resolved.profile_name == "baseline"
    assert resolved.profile.allowed_models == frozenset({"mock-model"})
    assert resolved.profile.requests_per_minute == 37
    assert resolved.profile.daily_budget.token_limit_daily == 1234
    assert resolved.profile.daily_budget.cost_limit_daily_usd == Decimal("2.50")
    assert resolved.profile.pii_action == "deny"
    assert resolved.profile.prompt_injection_action == "off"
    assert resolved.profile.allowed_tools == frozenset({"calculator", "lookup"})


def test_security_policy_registry_rejects_unknown_fields_and_non_integer_version() -> None:
    """
    RME

    Requires:
        - Policy documents contain an unsupported field or non-integer version.

    Modifies:
        - Nothing.

    Effects:
        - Verifies schema mistakes fail closed instead of being silently ignored.
        - Verifies JSON 1.0 is not accepted as integer schema version 1.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether strict validation rejects the documents.
    """
    document = _policy_document()
    profiles = document["profiles"]
    assert isinstance(profiles, dict)
    baseline = profiles["baseline"]
    assert isinstance(baseline, dict)
    baseline["unexpected"] = True

    with pytest.raises(ValueError):
        parse_security_policy_registry(json.dumps(document))

    version_document = _policy_document()
    version_document["version"] = 1.0
    with pytest.raises(ValueError):
        parse_security_policy_registry(json.dumps(version_document))


def test_startup_compiles_unified_profile_into_existing_enforcement_inputs(
    tmp_path,
    monkeypatch,
) -> None:
    """
    RME

    Requires:
        - A valid unified policy file is enabled before gateway startup.

    Modifies:
        - Temporary environment policy values and the process-local policy cache.

    Effects:
        - Verifies the unified registry replaces legacy per-control values.
        - Verifies all existing enforcement getters observe one coherent profile.

    Inputs:
        - tmp_path: Pytest temporary-directory fixture.
        - monkeypatch: Pytest environment-isolation fixture.

    Outputs:
        - None. Assertions determine whether startup compilation is coherent.
    """
    policy_path = tmp_path / "security-policies.json"
    policy_path.write_text(json.dumps(_policy_document()), encoding="utf-8")

    _track_policy_environment(monkeypatch)
    monkeypatch.setenv("SAG_SECURITY_POLICY_FILE", str(policy_path))
    clear_security_policy_cache()
    apply_unified_security_policy_environment()

    assert os.environ["SAG_SECURITY_POLICY_ACTIVE"] == "1"
    assert "SAG_SECURITY_POLICY_ERROR" not in os.environ
    assert get_client_allowed_models("test-client") == frozenset({"mock-model"})
    assert get_client_rate_limit("test-client") == 37

    budget = get_client_usage_budget("test-client")
    assert budget.token_limit_daily == 1234
    assert budget.cost_limit_daily_usd == Decimal("2.50")

    assert get_client_pii_policy("test-client").action == "deny"
    assert get_client_prompt_injection_policy("test-client").action == "off"
    assert get_client_allowed_tools("test-client") == frozenset(
        {"calculator", "lookup"}
    )


def test_enabled_invalid_unified_policy_clears_legacy_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    """
    RME

    Requires:
        - Unified policy mode is enabled with an invalid policy document.
        - Legacy per-control values may still exist during migration.

    Modifies:
        - Temporary environment policy values and the process-local policy cache.

    Effects:
        - Verifies invalid unified policy cannot fall back to stale legacy grants.
        - Leaves individual controls unconfigured so protected requests fail closed.

    Inputs:
        - tmp_path: Pytest temporary-directory fixture.
        - monkeypatch: Pytest environment-isolation fixture.

    Outputs:
        - None. Assertions determine whether migration fallback is prevented.
    """
    policy_path = tmp_path / "security-policies.json"
    policy_path.write_text("{}", encoding="utf-8")

    _track_policy_environment(monkeypatch)
    monkeypatch.setenv("SAG_SECURITY_POLICY_FILE", str(policy_path))
    clear_security_policy_cache()
    apply_unified_security_policy_environment()

    assert os.environ["SAG_SECURITY_POLICY_ERROR"] == "invalid_security_policy_file"
    assert "SAG_SECURITY_POLICY_ACTIVE" not in os.environ
    for variable in (
        "SAG_CLIENT_ALLOWED_MODELS",
        "SAG_CLIENT_RATE_LIMITS",
        "SAG_CLIENT_DAILY_BUDGETS",
        "SAG_CLIENT_PII_POLICIES",
        "SAG_CLIENT_PROMPT_INJECTION_POLICIES",
        "SAG_CLIENT_ALLOWED_TOOLS",
    ):
        assert variable not in os.environ
