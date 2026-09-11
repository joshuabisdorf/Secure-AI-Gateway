import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as JSONSchemaValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import ChatCompletionRequest, ChatCompletionResponse, ToolCall

_tool_name_pattern = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_execution_id_pattern = re.compile(r"^[0-9a-f]{32}$")
_supported_risks = frozenset({"read", "write", "destructive"})
_root_keys = frozenset({"version", "tools"})
_tool_keys = frozenset({"risk", "parameters"})
_ticket_keys = frozenset(
    {
        "v",
        "execution_id",
        "client_id",
        "key_id",
        "source_request_id",
        "tool_call_id",
        "tool_name",
        "arguments_sha256",
        "schema_sha256",
        "risk",
        "exp",
    }
)
_max_policy_bytes = 1_048_576
_max_ticket_seconds = 900
_default_ticket_seconds = 120

_registry_cache_lock = threading.Lock()
_registry_cache_signature: tuple[str, int, int] | None = None
_registry_cache: "ToolExecutionRegistry | None" = None


class ToolExecutionUnavailable(RuntimeError):
    """Raised when execution-time authorization infrastructure is unavailable."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ToolExecutionRejected(RuntimeError):
    """Raised when an untrusted model tool call cannot be authorized safely."""

    def __init__(
        self,
        reason: str,
        *,
        tool_name: str | None = None,
        risk: str | None = None,
    ) -> None:
        self.reason = reason
        self.tool_name = tool_name
        self.risk = risk
        super().__init__(reason)


@dataclass(frozen=True)
class ToolExecutionSpec:
    name: str
    risk: str
    parameters: dict[str, Any]
    schema_sha256: str


@dataclass(frozen=True)
class ToolExecutionRegistry:
    version: int
    tools: dict[str, ToolExecutionSpec]


@dataclass(frozen=True)
class ExecutionTicketMetadata:
    execution_id: str
    client_id: str
    key_id: str
    source_request_id: str
    tool_call_id: str
    tool_name: str
    arguments_sha256: str
    schema_sha256: str
    risk: str
    expires_at: int


@dataclass(frozen=True)
class PreparedToolExecution:
    execution_id: str
    tool_call_id: str
    tool_name: str
    risk: str


@dataclass(frozen=True)
class PreparedToolExecutionResponse:
    response: ChatCompletionResponse
    executions: tuple[PreparedToolExecution, ...]


class ToolExecutionReplayStore(Protocol):
    async def claim(self, execution_id: str, ttl_seconds: int) -> bool:
        """Atomically claim one execution authorization ID for one-time use."""
        ...

    async def close(self) -> None:
        """Release resources owned by the replay store."""
        ...


def _canonical_json(value: Any) -> bytes:
    """
    RME

    Requires:
        - value is JSON-serializable.

    Modifies:
        - Nothing.

    Effects:
        - Produces deterministic UTF-8 JSON for hashing/signing.

    Inputs:
        - value: JSON-compatible value.

    Outputs:
        - Canonical JSON bytes.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    """
    RME

    Requires:
        - value is JSON-serializable.

    Modifies:
        - Nothing.

    Effects:
        - Hashes canonical JSON without retaining it in authorization metadata.

    Inputs:
        - value: JSON-compatible value.

    Outputs:
        - Lowercase SHA-256 hexadecimal digest.
    """
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_text(value: str) -> str:
    """
    RME

    Requires:
        - value is text whose exact bytes must be bound to an execution ticket.

    Modifies:
        - Nothing.

    Effects:
        - Hashes UTF-8 text without copying it into ticket payload metadata.

    Inputs:
        - value: Source text.

    Outputs:
        - Lowercase SHA-256 hexadecimal digest.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys while parsing a trusted tool registry."""
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate_json_key")
        parsed[key] = value
    return parsed


def parse_tool_execution_registry(document: str) -> ToolExecutionRegistry:
    """
    RME

    Requires:
        - document is intended to be a version-1 execution-time tool registry.

    Modifies:
        - Nothing.

    Effects:
        - Strictly validates tool names, risk classes, and authoritative JSON Schemas.
        - Rejects unknown fields and malformed schemas.

    Inputs:
        - document: UTF-8 JSON registry document.

    Outputs:
        - Validated ToolExecutionRegistry.
    """
    try:
        root = json.loads(document, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_tool_execution_json") from exc
    if not isinstance(root, dict) or frozenset(root) != _root_keys:
        raise ValueError("invalid_tool_execution_root")
    version = root["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("unsupported_tool_execution_version")
    raw_tools = root["tools"]
    if not isinstance(raw_tools, dict):
        raise ValueError("invalid_tool_execution_tools")

    tools: dict[str, ToolExecutionSpec] = {}
    for name, raw_spec in raw_tools.items():
        if not isinstance(name, str) or not _tool_name_pattern.fullmatch(name):
            raise ValueError("invalid_tool_execution_name")
        if not isinstance(raw_spec, dict) or frozenset(raw_spec) != _tool_keys:
            raise ValueError("invalid_tool_execution_spec")
        risk = raw_spec["risk"]
        parameters = raw_spec["parameters"]
        if not isinstance(risk, str) or risk not in _supported_risks:
            raise ValueError("invalid_tool_execution_risk")
        if not isinstance(parameters, dict):
            raise ValueError("invalid_tool_execution_schema")
        try:
            Draft202012Validator.check_schema(parameters)
        except SchemaError as exc:
            raise ValueError("invalid_tool_execution_schema") from exc
        tools[name] = ToolExecutionSpec(
            name=name,
            risk=risk,
            parameters=parameters,
            schema_sha256=_sha256_json(parameters),
        )

    return ToolExecutionRegistry(version=version, tools=tools)


def load_tool_execution_registry(
    configured_path: str | None = None,
) -> ToolExecutionRegistry:
    """
    RME

    Requires:
        - configured_path or SAG_TOOL_EXECUTION_POLICY_FILE may identify a local JSON registry.

    Modifies:
        - Process-local registry cache when the file changes.

    Effects:
        - Loads and validates the authoritative tool execution registry.
        - Fails closed when the registry is absent, unreadable, oversized, or invalid.

    Inputs:
        - configured_path: Optional explicit registry path.

    Outputs:
        - Validated ToolExecutionRegistry.
    """
    path_value = configured_path or os.getenv("SAG_TOOL_EXECUTION_POLICY_FILE")
    if not path_value or not path_value.strip():
        raise ToolExecutionUnavailable("tool_execution_policy_not_configured")
    path = Path(path_value).expanduser()
    try:
        stat = path.stat()
    except OSError as exc:
        raise ToolExecutionUnavailable("tool_execution_policy_unavailable") from exc
    if stat.st_size > _max_policy_bytes:
        raise ToolExecutionUnavailable("tool_execution_policy_too_large")

    signature = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    global _registry_cache_signature, _registry_cache
    if _registry_cache_signature == signature and _registry_cache is not None:
        return _registry_cache

    with _registry_cache_lock:
        if _registry_cache_signature == signature and _registry_cache is not None:
            return _registry_cache
        try:
            document = path.read_text(encoding="utf-8")
            registry = parse_tool_execution_registry(document)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ToolExecutionUnavailable("invalid_tool_execution_policy") from exc
        _registry_cache_signature = signature
        _registry_cache = registry
        return registry


def _get_ticket_ttl_seconds() -> int:
    """
    RME

    Requires:
        - SAG_TOOL_EXECUTION_TTL_SECONDS may contain an integer from 1 through 900.

    Modifies:
        - Nothing.

    Effects:
        - Applies a short maximum lifetime to execution authorization tickets.

    Inputs:
        - None.

    Outputs:
        - Ticket lifetime in seconds.
    """
    raw = os.getenv("SAG_TOOL_EXECUTION_TTL_SECONDS", str(_default_ticket_seconds))
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ToolExecutionUnavailable("invalid_tool_execution_ttl") from exc
    if parsed < 1 or parsed > _max_ticket_seconds:
        raise ToolExecutionUnavailable("invalid_tool_execution_ttl")
    return parsed


def _get_signing_key() -> bytes:
    """
    RME

    Requires:
        - SAG_TOOL_EXECUTION_SIGNING_KEY contains at least 32 bytes of secret material.

    Modifies:
        - Nothing.

    Effects:
        - Fails closed rather than issuing unsigned or weak execution tickets.

    Inputs:
        - None.

    Outputs:
        - UTF-8 signing-key bytes.
    """
    configured = os.getenv("SAG_TOOL_EXECUTION_SIGNING_KEY")
    if configured is None:
        raise ToolExecutionUnavailable("tool_execution_signing_key_not_configured")
    key = configured.encode("utf-8")
    if len(key) < 32 or len(key) > 1024:
        raise ToolExecutionUnavailable("invalid_tool_execution_signing_key")
    return key


def _b64url_encode(value: bytes) -> str:
    """Encode bytes as unpadded URL-safe Base64."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Decode unpadded URL-safe Base64 or reject malformed input."""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ToolExecutionRejected("invalid_execution_ticket") from exc


def _issue_execution_ticket(
    *,
    client_id: str,
    key_id: str,
    source_request_id: str,
    tool_call: ToolCall,
    spec: ToolExecutionSpec,
) -> tuple[str, ExecutionTicketMetadata]:
    """
    RME

    Requires:
        - tool_call arguments have already passed the authoritative tool schema.
        - spec comes from the trusted execution registry.

    Modifies:
        - Nothing.

    Effects:
        - Issues a short-lived HMAC-SHA256 ticket without embedding raw tool arguments.

    Inputs:
        - client_id: Authenticated client identity.
        - key_id: Authenticated API-key identifier.
        - source_request_id: Chat request that produced the tool call.
        - tool_call: Validated model-generated function call.
        - spec: Authoritative execution specification.

    Outputs:
        - Signed token and safe metadata used for audit/verification.
    """
    now = int(time.time())
    metadata = ExecutionTicketMetadata(
        execution_id=uuid4().hex,
        client_id=client_id,
        key_id=key_id,
        source_request_id=source_request_id,
        tool_call_id=tool_call.id,
        tool_name=tool_call.function.name,
        arguments_sha256=_sha256_text(tool_call.function.arguments),
        schema_sha256=spec.schema_sha256,
        risk=spec.risk,
        expires_at=now + _get_ticket_ttl_seconds(),
    )
    payload = {
        "v": 1,
        "execution_id": metadata.execution_id,
        "client_id": metadata.client_id,
        "key_id": metadata.key_id,
        "source_request_id": metadata.source_request_id,
        "tool_call_id": metadata.tool_call_id,
        "tool_name": metadata.tool_name,
        "arguments_sha256": metadata.arguments_sha256,
        "schema_sha256": metadata.schema_sha256,
        "risk": metadata.risk,
        "exp": metadata.expires_at,
    }
    payload_bytes = _canonical_json(payload)
    signature = hmac.new(_get_signing_key(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}", metadata


def _validate_tool_arguments(tool_call: ToolCall, spec: ToolExecutionSpec) -> None:
    """
    RME

    Requires:
        - spec contains a validated Draft 2020-12 JSON Schema.

    Modifies:
        - Nothing.

    Effects:
        - Parses untrusted model arguments as JSON and validates them against the authoritative schema.

    Inputs:
        - tool_call: Model-generated function call.
        - spec: Trusted tool execution specification.

    Outputs:
        - None when arguments are valid; raises ToolExecutionRejected otherwise.
    """
    try:
        arguments = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError as exc:
        raise ToolExecutionRejected(
            "tool_arguments_not_json",
            tool_name=tool_call.function.name,
            risk=spec.risk,
        ) from exc
    try:
        Draft202012Validator(spec.parameters).validate(arguments)
    except JSONSchemaValidationError as exc:
        raise ToolExecutionRejected(
            "tool_arguments_invalid",
            tool_name=tool_call.function.name,
            risk=spec.risk,
        ) from exc


def prepare_tool_execution_response(
    response: ChatCompletionResponse,
    request: ChatCompletionRequest,
    *,
    client_id: str,
    key_id: str,
    source_request_id: str,
    allowed_tools: frozenset[str],
) -> PreparedToolExecutionResponse:
    """
    RME

    Requires:
        - response is a validated provider response for request.
        - allowed_tools is the authenticated client's current function allowlist.

    Modifies:
        - Nothing in the provider response or request.

    Effects:
        - Treats every model-generated tool call as untrusted output.
        - Requires each call to be declared in the source request and still allowed by policy.
        - Requires the request-declared parameter schema to match the authoritative execution registry.
        - Validates exact model arguments against the authoritative schema.
        - Attaches short-lived execution tickets and risk labels to safe copied tool calls.

    Inputs:
        - response: Upstream chat completion.
        - request: Sanitized request forwarded to the provider.
        - client_id: Authenticated client identity.
        - key_id: Authenticated API-key identifier.
        - source_request_id: Gateway request correlation ID.
        - allowed_tools: Current client tool allowlist.

    Outputs:
        - Copied response plus safe metadata for issued execution tickets.
    """
    total_calls = sum(
        len(choice.message.tool_calls or []) for choice in response.choices
    )
    if total_calls == 0:
        return PreparedToolExecutionResponse(response=response, executions=())

    registry = load_tool_execution_registry()
    declared = {
        tool.function.name: tool.function.parameters for tool in (request.tools or [])
    }
    seen_call_ids: set[str] = set()
    prepared: list[PreparedToolExecution] = []
    updated_choices = []

    for choice in response.choices:
        updated_calls = []
        for tool_call in choice.message.tool_calls or []:
            name = tool_call.function.name
            if tool_call.id in seen_call_ids:
                raise ToolExecutionRejected("duplicate_tool_call_id", tool_name=name)
            seen_call_ids.add(tool_call.id)
            if name not in allowed_tools:
                raise ToolExecutionRejected("tool_not_allowed", tool_name=name)
            declared_schema = declared.get(name)
            if declared_schema is None:
                raise ToolExecutionRejected("tool_not_declared", tool_name=name)
            spec = registry.tools.get(name)
            if spec is None:
                raise ToolExecutionRejected("tool_execution_policy_missing", tool_name=name)
            if _sha256_json(declared_schema) != spec.schema_sha256:
                raise ToolExecutionRejected(
                    "tool_schema_mismatch",
                    tool_name=name,
                    risk=spec.risk,
                )
            _validate_tool_arguments(tool_call, spec)
            token, metadata = _issue_execution_ticket(
                client_id=client_id,
                key_id=key_id,
                source_request_id=source_request_id,
                tool_call=tool_call,
                spec=spec,
            )
            updated_calls.append(
                tool_call.model_copy(
                    update={
                        "execution_token": token,
                        "execution_risk": spec.risk,
                    }
                )
            )
            prepared.append(
                PreparedToolExecution(
                    execution_id=metadata.execution_id,
                    tool_call_id=metadata.tool_call_id,
                    tool_name=metadata.tool_name,
                    risk=metadata.risk,
                )
            )

        updated_message = choice.message
        if choice.message.tool_calls is not None:
            updated_message = choice.message.model_copy(update={"tool_calls": updated_calls})
        updated_choices.append(choice.model_copy(update={"message": updated_message}))

    return PreparedToolExecutionResponse(
        response=response.model_copy(update={"choices": updated_choices}),
        executions=tuple(prepared),
    )


def verify_execution_ticket(
    token: str,
    tool_call: ToolCall,
    *,
    client_id: str,
    key_id: str,
) -> ExecutionTicketMetadata:
    """
    RME

    Requires:
        - token purports to authorize the exact supplied tool_call.
        - client_id/key_id identify the currently authenticated caller.

    Modifies:
        - Nothing.

    Effects:
        - Verifies ticket signature, expiry, identity, call ID/name, argument digest, schema fingerprint, and risk.
        - Re-resolves the current authoritative tool registry so stale policies fail closed.

    Inputs:
        - token: Gateway-issued execution authorization ticket.
        - tool_call: Exact tool call proposed for execution.
        - client_id: Current authenticated client.
        - key_id: Current authenticated gateway key identifier.

    Outputs:
        - Verified safe ticket metadata.
    """
    if len(token) > 4096 or token.count(".") != 1:
        raise ToolExecutionRejected("invalid_execution_ticket")
    payload_segment, signature_segment = token.split(".", 1)
    payload_bytes = _b64url_decode(payload_segment)
    signature = _b64url_decode(signature_segment)
    expected = hmac.new(_get_signing_key(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ToolExecutionRejected("invalid_execution_ticket")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ToolExecutionRejected("invalid_execution_ticket") from exc
    if not isinstance(payload, dict) or frozenset(payload) != _ticket_keys:
        raise ToolExecutionRejected("invalid_execution_ticket")

    required_strings = (
        "execution_id",
        "client_id",
        "key_id",
        "source_request_id",
        "tool_call_id",
        "tool_name",
        "arguments_sha256",
        "schema_sha256",
        "risk",
    )
    if any(not isinstance(payload.get(field), str) for field in required_strings):
        raise ToolExecutionRejected("invalid_execution_ticket")
    if payload.get("v") != 1 or not isinstance(payload.get("exp"), int):
        raise ToolExecutionRejected("invalid_execution_ticket")
    if not _execution_id_pattern.fullmatch(payload["execution_id"]):
        raise ToolExecutionRejected("invalid_execution_ticket")
    now = int(time.time())
    if payload["exp"] <= now:
        raise ToolExecutionRejected("execution_ticket_expired")
    if payload["exp"] > now + _max_ticket_seconds:
        raise ToolExecutionRejected("invalid_execution_ticket")
    if payload["client_id"] != client_id or payload["key_id"] != key_id:
        raise ToolExecutionRejected("execution_identity_mismatch")
    if payload["tool_call_id"] != tool_call.id:
        raise ToolExecutionRejected("tool_call_id_mismatch")
    if payload["tool_name"] != tool_call.function.name:
        raise ToolExecutionRejected("tool_name_mismatch")
    if payload["arguments_sha256"] != _sha256_text(tool_call.function.arguments):
        raise ToolExecutionRejected("tool_arguments_changed")

    registry = load_tool_execution_registry()
    spec = registry.tools.get(payload["tool_name"])
    if spec is None:
        raise ToolExecutionRejected("tool_execution_policy_missing")
    if payload["schema_sha256"] != spec.schema_sha256 or payload["risk"] != spec.risk:
        raise ToolExecutionRejected(
            "tool_execution_policy_changed",
            tool_name=spec.name,
            risk=spec.risk,
        )
    _validate_tool_arguments(tool_call, spec)

    return ExecutionTicketMetadata(
        execution_id=payload["execution_id"],
        client_id=payload["client_id"],
        key_id=payload["key_id"],
        source_request_id=payload["source_request_id"],
        tool_call_id=payload["tool_call_id"],
        tool_name=payload["tool_name"],
        arguments_sha256=payload["arguments_sha256"],
        schema_sha256=payload["schema_sha256"],
        risk=payload["risk"],
        expires_at=payload["exp"],
    )


class InMemoryToolExecutionReplayStore:
    def __init__(self) -> None:
        """
        RME

        Requires:
            - Used only in deterministic single-process tests/development.

        Modifies:
            - Initializes process-local replay state.

        Effects:
            - Provides one-time ticket claiming without network services.

        Inputs:
            - None.

        Outputs:
            - Replay-store instance.
        """
        self._claimed: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def claim(self, execution_id: str, ttl_seconds: int) -> bool:
        """
        RME

        Requires:
            - execution_id is a validated ticket ID.
            - ttl_seconds is positive.

        Modifies:
            - Process-local claimed-ID state.

        Effects:
            - Atomically rejects an execution ID that is still claimed.

        Inputs:
            - execution_id: Ticket execution identifier.
            - ttl_seconds: Remaining ticket lifetime.

        Outputs:
            - True only for the first valid claim within the TTL.
        """
        now = time.monotonic()
        async with self._lock:
            self._claimed = {
                key: expiry for key, expiry in self._claimed.items() if expiry > now
            }
            if execution_id in self._claimed:
                return False
            self._claimed[execution_id] = now + ttl_seconds
            return True

    def reset(self) -> None:
        """Clear process-local replay state for deterministic tests."""
        self._claimed.clear()

    async def close(self) -> None:
        """No-op close for the process-local replay store."""
        return None


class RedisToolExecutionReplayStore:
    def __init__(self, redis_url: str) -> None:
        """
        RME

        Requires:
            - redis_url identifies the shared Redis service.

        Modifies:
            - Initializes a lazy Redis client.

        Effects:
            - Prepares distributed one-time execution-ticket replay protection.

        Inputs:
            - redis_url: Redis connection URL.

        Outputs:
            - Redis-backed replay-store instance.
        """
        self._client = Redis.from_url(redis_url, decode_responses=True)

    async def claim(self, execution_id: str, ttl_seconds: int) -> bool:
        """
        RME

        Requires:
            - Redis is available.
            - execution_id is a validated ticket ID.

        Modifies:
            - Shared Redis replay state under sag:tool_execution:*.

        Effects:
            - Uses SET NX with expiry so only one gateway replica can authorize the ticket.
            - Fails closed when Redis is unavailable.

        Inputs:
            - execution_id: Ticket execution identifier.
            - ttl_seconds: Remaining ticket lifetime.

        Outputs:
            - True only when the execution ID was newly claimed.
        """
        try:
            result = await self._client.set(
                f"sag:tool_execution:{execution_id}",
                "1",
                ex=max(1, ttl_seconds),
                nx=True,
            )
        except RedisError as exc:
            raise ToolExecutionUnavailable("tool_execution_replay_store_unavailable") from exc
        return bool(result)

    async def close(self) -> None:
        """Close the Redis connection pool owned by this replay store."""
        await self._client.aclose()


def build_tool_execution_replay_store() -> ToolExecutionReplayStore:
    """
    RME

    Requires:
        - SAG_TOOL_EXECUTION_REPLAY_BACKEND is redis or memory when configured.
        - REDIS_URL is configured for the redis backend.

    Modifies:
        - Nothing outside the returned backend object.

    Effects:
        - Selects distributed replay protection for runtime and memory for deterministic tests.

    Inputs:
        - None.

    Outputs:
        - Configured ToolExecutionReplayStore.
    """
    backend = os.getenv("SAG_TOOL_EXECUTION_REPLAY_BACKEND", "redis").strip().lower()
    if backend == "memory":
        return InMemoryToolExecutionReplayStore()
    if backend == "redis":
        redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
        if not redis_url:
            raise ToolExecutionUnavailable("tool_execution_replay_store_not_configured")
        return RedisToolExecutionReplayStore(redis_url)
    raise ToolExecutionUnavailable("unsupported_tool_execution_replay_backend")
