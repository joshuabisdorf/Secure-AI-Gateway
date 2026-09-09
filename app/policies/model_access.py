import os

from fastapi import HTTPException, status


def get_allowed_models() -> frozenset[str]:
    """
    RME

    Requires:
        - SAG_ALLOWED_MODELS may be configured as a comma-separated list.

    Modifies:
        - Nothing.

    Effects:
        - Rejects policy evaluation when no usable model allowlist is configured.

    Inputs:
        - None.

    Outputs:
        - A frozen set containing the configured model names.
    """
    configured_models = os.getenv("SAG_ALLOWED_MODELS")

    if configured_models is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model access policy is not configured.",
        )

    allowed_models = frozenset(
        model.strip()
        for model in configured_models.split(",")
        if model.strip()
    )

    if not allowed_models:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model access policy is not configured.",
        )

    return allowed_models


def enforce_model_allowed(model: str) -> None:
    """
    RME

    Requires:
        - model is the normalized model name requested by the caller.
        - SAG_ALLOWED_MODELS contains at least one configured model.

    Modifies:
        - Nothing.

    Effects:
        - Rejects requests for models that are not explicitly allowed.

    Inputs:
        - model: Requested model name.

    Outputs:
        - None. Successful return means the model is allowed.
    """
    if model not in get_allowed_models():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requested model is not allowed.",
        )
