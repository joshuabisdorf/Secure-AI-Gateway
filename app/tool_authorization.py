import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.models import ChatCompletionRequest, NamedToolChoice

_client_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_tool_name_pattern = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolAuthorizationDecision:
    allowed: bool
    requested_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    reason: str | None = None


def parse_client_allowed_tools(configured_tools: str) -> dict[str, frozenset[str]]:
    """
    RME

    Requires:
        - configured_tools uses client_id:tool_name records separated by commas.
        - A single hyphen explicitly grants no tools to a client.

    Modifies:
        - Nothing.

    Effects:
        - Validates per-client function-tool allowlists.
        - Rejects malformed records, duplicate grants, and mixed no-tool/tool grants.

    Inputs:
        - configured_tools: Serialized per-client tool authorization records.

    Outputs:
        - Mapping from client ID to an immutable set of allowed function names.

    Raises:
        - ValueError: Configuration is empty, malformed, duplicated, or contradictory.
    """
    mutable: dict[str, set[str]] = {}
    explicitly_none: set[str] = set()

    for raw_record in configured_tools.split(","):
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        parts = raw_record.split(":", 1)
        if len(parts) != 2:
            raise ValueError("invalid_tool_authorization_record")

        client_id, tool_name = (part.strip() for part in parts)
        if not _client_id_pattern.fullmatch(client_id):
            raise ValueError("invalid_client_id")
        if not tool_name:
            raise ValueError("invalid_tool_name")

        if tool_name == "-":
            if client_id in explicitly_none or mutable.get(client_id):
                raise ValueError("contradictory_tool_authorization")
            explicitly_none.add(client_id)
            mutable.setdefault(client_id, set())
            continue

        if not _tool_name_pattern.fullmatch(tool_name):
            raise ValueError("invalid_tool_name")
        if client_id in explicitly_none:
            raise ValueError("contradictory_tool_authorization")

        granted = mutable.setdefault(client_id, set())
        if tool_name in granted:
            raise ValueError("duplicate_tool_grant")
        granted.add(tool_name)

    if not mutable:
        raise ValueError("no_client_tool_authorization")

    return {client_id: frozenset(names) for client_id, names in mutable.items()}


def get_client_allowed_tools(client_id: str) -> frozenset[str]:
    """
    RME

    Requires:
        - client_id identifies an authenticated gateway client.
        - SAG_CLIENT_ALLOWED_TOOLS may define per-client function-tool grants.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed when tool authorization is absent, malformed, or missing the client.

    Inputs:
        - client_id: Authenticated gateway client identity.

    Outputs:
        - Immutable set of function-tool names the client may expose to the model.
    """
    configured_tools = os.getenv("SAG_CLIENT_ALLOWED_TOOLS")
    if not configured_tools:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool authorization policy is not configured.",
        )

    try:
        allowed = parse_client_allowed_tools(configured_tools)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool authorization policy is not configured.",
        ) from None

    client_tools = allowed.get(client_id)
    if client_tools is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool authorization policy is not configured for this client.",
        )

    return client_tools


def authorize_request_tools(
    request: ChatCompletionRequest,
    allowed_tools: frozenset[str],
) -> ToolAuthorizationDecision:
    """
    RME

    Requires:
        - request is a validated chat-completion request.
        - allowed_tools is the authenticated client's function-tool allowlist.

    Modifies:
        - Nothing.

    Effects:
        - Verifies every tool exposed to the model is explicitly allowed.
        - Verifies a named tool_choice refers to a declared and allowed function.
        - Applies least privilege without executing any tool.

    Inputs:
        - request: Validated request containing optional function tools/tool choice.
        - allowed_tools: Function names authorized for the authenticated client.

    Outputs:
        - ToolAuthorizationDecision with safe requested/denied tool names and reason.
    """
    requested = tuple(
        dict.fromkeys(tool.function.name for tool in (request.tools or []))
    )
    requested_set = set(requested)

    denied = tuple(name for name in requested if name not in allowed_tools)
    if denied:
        return ToolAuthorizationDecision(
            allowed=False,
            requested_tools=requested,
            denied_tools=denied,
            reason="tool_not_allowed",
        )

    if isinstance(request.tool_choice, NamedToolChoice):
        choice_name = request.tool_choice.function.name
        if choice_name not in requested_set:
            return ToolAuthorizationDecision(
                allowed=False,
                requested_tools=requested,
                denied_tools=(choice_name,),
                reason="tool_choice_not_declared",
            )
        if choice_name not in allowed_tools:
            return ToolAuthorizationDecision(
                allowed=False,
                requested_tools=requested,
                denied_tools=(choice_name,),
                reason="tool_not_allowed",
            )

    return ToolAuthorizationDecision(
        allowed=True,
        requested_tools=requested,
        denied_tools=(),
    )
