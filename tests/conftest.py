import os

import pytest

# Keep the test suite deterministic, offline, and free of provider API charges.
# This must run during test collection, before test modules import app.main.
os.environ["SAG_PROVIDER"] = "mock"
os.environ["SAG_CLIENT_RATE_LIMITS"] = "test-client:10000"
os.environ["SAG_CLIENT_DAILY_BUDGETS"] = "test-client:1000000:1000.00"

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
        - The gateway exposes process-local rate-limit and usage-ledger state.

    Modifies:
        - Process-local gateway rate-limit and usage-budget state around each test.

    Effects:
        - Prevents request counts and usage totals from leaking between tests.

    Inputs:
        - None.

    Outputs:
        - None. Fixture setup and teardown isolate process-local state.
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
        - Temporarily configures SAG_CLIENTS for the current test.

    Effects:
        - Provides a raw test client key whose hash appears in SAG_CLIENTS.

    Inputs:
        - monkeypatch: pytest fixture used to configure the test environment.

    Outputs:
        - Raw gateway API key for the configured test client.
    """
    monkeypatch.setenv("SAG_CLIENTS", TEST_CLIENTS)
    return TEST_API_KEY
