from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_tool_name_pattern = r"^[A-Za-z0-9_-]{1,64}$"
ToolRisk = Literal["read", "write", "destructive"]

MAX_MODEL_LENGTH = 256
MAX_MESSAGES = 64
MAX_MESSAGE_CONTENT_LENGTH = 131_072
MAX_TOOL_CALLS_PER_MESSAGE = 32
MAX_TOOLS = 64
MAX_TOOL_DESCRIPTION_LENGTH = 4_096
MAX_TOOL_ARGUMENT_LENGTH = 65_536
MAX_TOOL_SCHEMA_DEPTH = 16
MAX_TOOL_SCHEMA_NODES = 4_096
MAX_TOOL_SCHEMA_TEXT_CHARS = 65_536
MAX_REQUEST_TEXT_CHARS = 262_144
MAX_RESPONSE_CHOICES = 16


def _measure_json_shape(value: Any, *, depth: int = 0) -> tuple[int, int]:
    """
    RME

    Requires:
        - value is JSON-like data supplied by a validated request model.

    Modifies:
        - Nothing.

    Effects:
        - Rejects tool-schema structures deeper than MAX_TOOL_SCHEMA_DEPTH.
        - Counts container/scalar nodes and textual characters without serializing secrets.

    Inputs:
        - value: JSON-like value to measure.
        - depth: Current recursive depth.

    Outputs:
        - Tuple of node count and textual character count.
    """
    if depth > MAX_TOOL_SCHEMA_DEPTH:
        raise ValueError("tool schema exceeds maximum nesting depth")

    nodes = 1
    text_chars = 0

    if isinstance(value, dict):
        for key, child in value.items():
            text_chars += len(str(key))
            child_nodes, child_chars = _measure_json_shape(child, depth=depth + 1)
            nodes += child_nodes
            text_chars += child_chars
    elif isinstance(value, (list, tuple)):
        for child in value:
            child_nodes, child_chars = _measure_json_shape(child, depth=depth + 1)
            nodes += child_nodes
            text_chars += child_chars
    elif isinstance(value, str):
        text_chars += len(value)
    elif value is not None:
        text_chars += len(str(value))

    return nodes, text_chars


class ToolFunction(BaseModel):
    name: str = Field(pattern=_tool_name_pattern)
    description: str | None = Field(default=None, max_length=MAX_TOOL_DESCRIPTION_LENGTH)
    parameters: dict[str, Any] = Field(default_factory=dict)
    strict: bool | None = None

    @model_validator(mode="after")
    def validate_schema_complexity(self) -> "ToolFunction":
        """
        RME

        Requires:
            - parameters contains JSON-schema-like data.

        Modifies:
            - Nothing.

        Effects:
            - Rejects excessively deep, large, or text-heavy tool schemas.

        Inputs:
            - self: Validated tool function candidate.

        Outputs:
            - The same ToolFunction when complexity is within bounds.
        """
        nodes, text_chars = _measure_json_shape(self.parameters)
        if nodes > MAX_TOOL_SCHEMA_NODES:
            raise ValueError("tool schema exceeds maximum node count")
        if text_chars > MAX_TOOL_SCHEMA_TEXT_CHARS:
            raise ValueError("tool schema exceeds maximum text size")
        return self


class ChatTool(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class NamedToolChoiceFunction(BaseModel):
    name: str = Field(pattern=_tool_name_pattern)


class NamedToolChoice(BaseModel):
    type: Literal["function"] = "function"
    function: NamedToolChoiceFunction


ToolChoice = Literal["none", "auto", "required"] | NamedToolChoice


class ToolCallFunction(BaseModel):
    name: str = Field(pattern=_tool_name_pattern)
    arguments: str = Field(max_length=MAX_TOOL_ARGUMENT_LENGTH)


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["function"] = "function"
    function: ToolCallFunction
    execution_token: str | None = Field(default=None, max_length=4096)
    execution_risk: ToolRisk | None = None


class ChatMessage(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: str | None = Field(default=None, max_length=MAX_MESSAGE_CONTENT_LENGTH)
    name: str | None = Field(default=None, max_length=64)
    tool_call_id: str | None = Field(default=None, max_length=128)
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        max_length=MAX_TOOL_CALLS_PER_MESSAGE,
    )


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=MAX_MODEL_LENGTH)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    tools: list[ChatTool] | None = Field(default=None, max_length=MAX_TOOLS)
    tool_choice: ToolChoice | None = None
    stream: Literal[False] = False

    @model_validator(mode="after")
    def validate_aggregate_complexity(self) -> "ChatCompletionRequest":
        """
        RME

        Requires:
            - Nested request fields have passed their individual bounds.

        Modifies:
            - Nothing.

        Effects:
            - Rejects requests whose combined text/schema content exceeds the gateway budget.

        Inputs:
            - self: Validated chat-completion request candidate.

        Outputs:
            - The same ChatCompletionRequest when aggregate complexity is within bounds.
        """
        text_chars = len(self.model)

        for message in self.messages:
            text_chars += len(message.role)
            text_chars += len(message.content or "")
            text_chars += len(message.name or "")
            text_chars += len(message.tool_call_id or "")
            for tool_call in message.tool_calls or ():
                text_chars += len(tool_call.id)
                text_chars += len(tool_call.function.name)
                text_chars += len(tool_call.function.arguments)

        for tool in self.tools or ():
            text_chars += len(tool.function.name)
            text_chars += len(tool.function.description or "")
            _, schema_chars = _measure_json_shape(tool.function.parameters)
            text_chars += schema_chars

        if text_chars > MAX_REQUEST_TEXT_CHARS:
            raise ValueError("request exceeds maximum aggregate text size")
        return self


class ChoiceMessage(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: str | None = Field(default=None, max_length=MAX_MESSAGE_CONTENT_LENGTH)
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        max_length=MAX_TOOL_CALLS_PER_MESSAGE,
    )


class ChatChoice(BaseModel):
    index: int = Field(ge=0)
    message: ChoiceMessage
    finish_reason: str = Field(max_length=64)


class ChatUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ChatCompletionResponse(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    object: str = Field(default="chat.completion", max_length=64)
    model: str = Field(min_length=1, max_length=MAX_MODEL_LENGTH)
    choices: list[ChatChoice] = Field(max_length=MAX_RESPONSE_CHOICES)
    usage: ChatUsage | None = None


class ToolExecutionAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call: ToolCall


class ToolExecutionAuthorizationResponse(BaseModel):
    authorized: Literal[True] = True
    execution_id: str = Field(min_length=1, max_length=256)
    source_request_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(pattern=_tool_name_pattern)
    risk: ToolRisk
