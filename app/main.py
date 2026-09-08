from fastapi import FastAPI

app = FastAPI(
    title="Secure AI Gateway",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """
    RME

    Requires:
        - The FastAPI application is running.

    Modifies:
        - Nothing.

    Effects:
        - Reports the health status of the gateway.

    Inputs:
        - None.

    Outputs:
        - A dictionary containing the gateway health status.
    """
    return {"status": "ok"}
