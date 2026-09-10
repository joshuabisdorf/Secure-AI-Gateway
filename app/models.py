from typing import Any, Literal

from pydantic import BaseModel, Field

_tool_name_pattern = r"^[A-Za-z0-9_-]{1,64}$"


class ToolFunction(BaseModel):
    name: str = Field(pattern=_tool_name_pattern)
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    strict: bool | None = None


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
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    tools: list[ChatTool] | None = None
    tool_choice: ToolChoice | None = None


class ChoiceMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatChoice(BaseModel):
    index: int
    message: ChoiceMessage
    finish_reason: str


class ChatUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    model: str
    choices: list[ChatChoice]
    usage: ChatUsage | None = None
