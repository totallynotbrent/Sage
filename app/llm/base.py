from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant", "tool"]

FinishReason = Literal["stop", "tool_calls", "length", "error"]

TOOL_STATUS_ARGUMENT = "status"
TOOL_STATUS_PARAMETER = {
    "type": "string",
    "description": (
        "A concise natural user-facing description of what the assistant is doing before "
        "this tool runs. Write it in the assistant's current style without hidden reasoning."
    ),
}


def tool_parameters_with_status(parameters: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(parameters or {"type": "object", "properties": {}})
    properties = result.get("properties")
    result["properties"] = dict(properties) if isinstance(properties, dict) else {}
    result["properties"][TOOL_STATUS_ARGUMENT] = dict(TOOL_STATUS_PARAMETER)
    required = result.get("required")
    required_values = list(required) if isinstance(required, list) else []
    if TOOL_STATUS_ARGUMENT not in required_values:
        required_values.append(TOOL_STATUS_ARGUMENT)
    result["required"] = required_values
    return result


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMMessage:
    role: MessageRole
    content: str = ""
    thinking: str = ""
    images: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None

    def is_tool_result(self) -> bool:
        return self.role == "tool"


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMUsage:
    model: str | None = None
    total_duration_ns: int | None = None
    load_duration_ns: int | None = None
    prompt_eval_count: int | None = None
    prompt_eval_duration_ns: int | None = None
    eval_count: int | None = None
    eval_duration_ns: int | None = None
    logprobs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def output_tokens_per_sec(self) -> float | None:
        if self.eval_count is None or not self.eval_duration_ns:
            return None
        return round(self.eval_count / (self.eval_duration_ns / 1_000_000_000), 2)

    @property
    def prompt_tokens_per_sec(self) -> float | None:
        if self.prompt_eval_count is None or not self.prompt_eval_duration_ns:
            return None
        return round(self.prompt_eval_count / (self.prompt_eval_duration_ns / 1_000_000_000), 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "total_duration_ns": self.total_duration_ns,
            "load_duration_ns": self.load_duration_ns,
            "prompt_eval_count": self.prompt_eval_count,
            "prompt_eval_duration_ns": self.prompt_eval_duration_ns,
            "eval_count": self.eval_count,
            "eval_duration_ns": self.eval_duration_ns,
            "output_tokens_per_sec": self.output_tokens_per_sec,
            "prompt_tokens_per_sec": self.prompt_tokens_per_sec,
            "logprobs": self.logprobs,
        }

    @classmethod
    def aggregate(cls, usages: list["LLMUsage"]) -> "LLMUsage":
        values = [usage for usage in usages if usage is not None]
        return cls(
            model=next((usage.model for usage in reversed(values) if usage.model), None),
            total_duration_ns=_sum_usage(values, "total_duration_ns"),
            load_duration_ns=_sum_usage(values, "load_duration_ns"),
            prompt_eval_count=_sum_usage(values, "prompt_eval_count"),
            prompt_eval_duration_ns=_sum_usage(values, "prompt_eval_duration_ns"),
            eval_count=_sum_usage(values, "eval_count"),
            eval_duration_ns=_sum_usage(values, "eval_duration_ns"),
            logprobs=[item for usage in values for item in usage.logprobs],
        )


def _sum_usage(usages: list[LLMUsage], field_name: str) -> int | None:
    values = [getattr(usage, field_name) for usage in usages]
    numeric = [int(value) for value in values if value is not None]
    return sum(numeric) if numeric else None


@dataclass
class StreamChunk:
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    finish_reason: FinishReason | None = None
    usage: LLMUsage | None = None
    raw: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason | None = None
    done: bool = True
    usage: LLMUsage | None = None

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient(ABC):
    @abstractmethod
    def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        images: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    @abstractmethod
    async def check_connection(self) -> bool:
        raise NotImplementedError

    async def close(self) -> None:
        return None

    @staticmethod
    def build_tool_schemas(tools: list[ToolSchema]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool_parameters_with_status(tool.parameters),
                },
            }
            for tool in tools
        ]

    @staticmethod
    def messages_to_dicts(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            entry: dict[str, Any] = {"role": message.role}
            if message.content:
                entry["content"] = message.content
            if message.thinking:
                entry["thinking"] = message.thinking
            if message.images:
                entry["images"] = list(message.images)
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    }
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                if message.tool_name:
                    entry["tool_name"] = message.tool_name
                if message.tool_call_id:
                    entry["tool_call_id"] = message.tool_call_id
            result.append(entry)
        return result
