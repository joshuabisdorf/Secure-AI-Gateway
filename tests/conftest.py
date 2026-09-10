import os

import pytest

# Keep the test suite deterministic, offline, database-free, Redis-free, and free of
# provider API charges. This must run during test collection, before test modules import
# app.main/app.auth. Locally exported runtime policy/backend settings must not override tests.
os.environ.pop("SAG_SECURITY_POLICY_FILE", None)
os.environ.pop("SAG_SECURITY_POLICY_ACTIVE", None)
os.environ.pop("SAG_SECURITY_POLICY_ERROR", None)
os.environ["SAG_PROVIDER"] = "mock"
os.environ["SAG_CLIENT_REGISTRY_BACKEND"] = "environment"
os.environ["SAG_USAGE_LEDGER_BACKEND"] = "memory"
os.environ["SAG_RATE_LIMIT_BACKEND"] = "memory"
os.environ["SAG_SEMANTIC_PII_BACKEND"] = "spacy"
os.environ["SAG_CLIENT_RATE_LIMITS"] = "test-client:10000"
os.environ["SAG_CLIENT_DAILY_BUDGETS"] = "test-client:1000000:1000.00"
os.environ["SAG_CLIENT_PII_POLICIES"] = "test-client:redact"
os.environ["SAG_CLIENT_PROMPT_INJECTION_POLICIES"] = "test-client:audit"
os.environ["SAG_CLIENT_ALLOWED_TOOLS"] = "test-client:-"

TEST_API_KEY = "sag_testkey_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
TEST_CLIENTS = (
    "test-client:testkey:"
    "669bba2c02863fea461a24f2c61758d0572eaf341680e20fbb17618585a86a13"
)


@pytest.fixture(autouse=True)
def reset_process_local_policy_state():
    """
    RME

    Requires:
        - Tests configure in-memory rate-limit and usage-ledger backends.

    Modifies:
        - Process-local gateway rate-limit and test usage-budget state around each test.

    Effects:
        - Prevents request counts and usage totals from leaking between tests.

    Inputs:
        - None.

    Outputs:
        - None. Fixture setup and teardown isolate process-local test state.
    """
    from app.main import rate_limiter, usage_ledger

    rate_limiter.reset()
    usage_ledger.reset()
    yield
    rate_limiter.reset()
    usage_ledger.reset()


@pytest.fixture
def gateway_api_key(monkeypatch) -> str:
    """
    RME

    Requires:
        - pytest provides the monkeypatch fixture.

    Modifies:
        - Temporarily configures the test-only environment client registry.

    Effects:
        - Provides a raw test client key whose hash appears in SAG_CLIENTS.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - Raw gateway API key for the configured test client.
    """
    monkeypatch.setenv("SAG_CLIENTS", TEST_CLIENTS)
    return TEST_API_KEY
