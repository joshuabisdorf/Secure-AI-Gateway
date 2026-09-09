import os
import re

from fastapi import HTTPException, status

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_model_name_pattern = re.compile(r"^[^\s,]{1,256}$")


def get_allowed_models() -> frozenset[str]:
    """
    RME

    Requires:
        - SAG_ALLOWED_MODELS may be configured as a comma-separated list.

    Modifies:
        - Nothing.

    Effects:
        - Rejects policy evaluation when no usable global model allowlist is configured.

    Inputs:
        - None.

    Outputs:
        - A frozen set containing globally allowed model names.
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


def parse_client_allowed_models(
    configured_policies: str,
) -> dict[str, frozenset[str]]:
    """
    RME

    Requires:
        - configured_policies uses client_id:model records separated by commas.
        - A client ID may appear more than once to allow multiple models.

    Modifies:
        - Nothing.

    Effects:
        - Validates per-client model policy configuration.
        - Groups allowed models by client identity.

    Inputs:
        - configured_policies: Serialized per-client model allowlist records.

    Outputs:
        - Mapping from client ID to its frozen set of allowed model names.

    Raises:
        - ValueError: The configuration is empty or contains an invalid record.
    """
    policies: dict[str, set[str]] = {}

    for raw_record in configured_policies.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":", 1)
        if len(parts) != 2:
            raise ValueError("invalid_client_model_record")

        client_id, model = (part.strip() for part in parts)
        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if not _model_name_pattern.fullmatch(model):
            raise ValueError("invalid_model_name")

        policies.setdefault(client_id, set()).add(model)

    if not policies:
        raise ValueError("no_client_model_policies")

    return {
        client_id: frozenset(models)
        for client_id, models in policies.items()
    }


def get_client_allowed_models(client_id: str) -> frozenset[str]:
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_CLIENT_ALLOWED_MODELS may contain per-client model records.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed when the client-policy registry is absent or malformed.
        - Denies authenticated clients that have no configured model grants.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Frozen set of models granted to the client.
    """
    configured_policies = os.getenv("SAG_CLIENT_ALLOWED_MODELS")
    if not configured_policies:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Client model access policy is not configured.",
        )

    try:
        policies = parse_client_allowed_models(configured_policies)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Client model access policy is not configured.",
        ) from None

    allowed_models = policies.get(client_id)
    if not allowed_models:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requested model is not allowed.",
        )

    return allowed_models


def enforce_model_allowed(model: str, client_id: str) -> None:
    """
    RME

    Requires:
        - model is the normalized model name requested by the caller.
        - client_id identifies the authenticated gateway client.
        - Global and per-client model policies are configured.

    Modifies:
        - Nothing.

    Effects:
        - Enforces the global model allowlist as a deployment-wide ceiling.
        - Enforces the authenticated client's model grants as a second restriction.
        - Rejects requests unless both policy layers allow the requested model.

    Inputs:
        - model: Requested model name.
        - client_id: Authenticated gateway client identity.

    Outputs:
        - None. Successful return means both policy layers allow the model.
    """
    if model not in get_allowed_models():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requested model is not allowed.",
        )

    if model not in get_client_allowed_models(client_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requested model is not allowed.",
        )
