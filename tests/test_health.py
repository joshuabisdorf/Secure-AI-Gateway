from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint() -> None:
    """
    RME

    Requires:
        - The FastAPI application can be imported.

    Modifies:
        - Nothing.

    Effects:
        - Sends a test request to the health endpoint.

    Inputs:
        - None.

    Outputs:
        - None. The test passes or fails through assertions.
    """
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
