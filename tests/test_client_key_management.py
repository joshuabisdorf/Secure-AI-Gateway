import asyncio
from contextlib import asynccontextmanager

from app.api_keys import ClientKeyRecord
from app import clients


class FakeCursor:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self._one = None
        self._all: list[tuple[object, ...]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        normalized = " ".join(query.split())
        keys = self.state["keys"]
        client_id = self.state["client_id"]
        client_active = self.state["client_active"]

        if "SELECT is_active FROM gateway_clients" in normalized:
            self._one = (client_active,) if params[0] == client_id else None
            self._all = []
            return

        if "SELECT key_id FROM gateway_api_keys" in normalized:
            requested_client_id = params[0]
            self._all = [
                (key_id,)
                for key_id, key in keys.items()
                if key["client_id"] == requested_client_id and key["is_active"]
            ]
            self._one = None
            return

        if "INSERT INTO gateway_api_keys" in normalized:
            key_id, inserted_client_id, api_key_sha256 = params
            if key_id in keys:
                self._one = None
                self._all = []
                return
            keys[key_id] = {
                "client_id": inserted_client_id,
                "api_key_sha256": api_key_sha256,
                "is_active": True,
            }
            self._one = (key_id,)
            self._all = []
            return

        if "UPDATE gateway_api_keys" in normalized and "key_id <>" in normalized:
            requested_client_id, replacement_key_id = params
            revoked: list[tuple[object, ...]] = []
            for key_id, key in keys.items():
                if (
                    key["client_id"] == requested_client_id
                    and key_id != replacement_key_id
                    and key["is_active"]
                ):
                    key["is_active"] = False
                    revoked.append((key_id,))
            self._all = revoked
            self._one = None
            return

        if "UPDATE gateway_api_keys" in normalized:
            key_id = params[0]
            key = keys.get(key_id)
            if key is not None and key["is_active"]:
                key["is_active"] = False
                self._one = (key["client_id"],)
            else:
                self._one = None
            self._all = []
            return

        if "SELECT 1 FROM gateway_api_keys" in normalized:
            self._one = (1,) if params[0] in keys else None
            self._all = []
            return

        raise AssertionError(f"Unexpected SQL in test: {normalized}")

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._all


class FakeConnection:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.state)

    @asynccontextmanager
    async def transaction(self):
        yield


async def _fake_connect_factory(state: dict[str, object], database_url: str):
    assert database_url == "postgresql://test"
    return FakeConnection(state)


def test_rotate_revokes_old_key_and_revoke_is_idempotent(monkeypatch) -> None:
    """
    RME

    Requires:
        - Key-management functions can use a deterministic fake database connection.

    Modifies:
        - In-memory fake client/key state.

    Effects:
        - Verifies rotation inserts a replacement key and revokes prior active keys.
        - Verifies explicit revocation is idempotent for an already-revoked key.

    Inputs:
        - monkeypatch: pytest fixture used to inject deterministic dependencies.

    Outputs:
        - None. Assertions determine whether key lifecycle behavior is correct.
    """
    state: dict[str, object] = {
        "client_id": "local-dev",
        "client_active": True,
        "keys": {
            "oldkey": {
                "client_id": "local-dev",
                "api_key_sha256": "a" * 64,
                "is_active": True,
            }
        },
    }

    async def fake_connect(database_url: str):
        return await _fake_connect_factory(state, database_url)

    monkeypatch.setattr(
        clients.psycopg.AsyncConnection,
        "connect",
        staticmethod(fake_connect),
    )
    monkeypatch.setattr(
        clients,
        "generate_api_key",
        lambda client_id: (
            "sag_newkey_new-secret",
            ClientKeyRecord(
                client_id=client_id,
                key_id="newkey",
                api_key_sha256="b" * 64,
            ),
        ),
    )

    api_key, key_id, revoked = asyncio.run(
        clients.rotate_client_keys("postgresql://test", "local-dev")
    )

    assert api_key == "sag_newkey_new-secret"
    assert key_id == "newkey"
    assert revoked == ("oldkey",)
    assert state["keys"]["oldkey"]["is_active"] is False
    assert state["keys"]["newkey"]["is_active"] is True

    changed = asyncio.run(clients.revoke_client_key("postgresql://test", "oldkey"))
    assert changed is False
