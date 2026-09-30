"""
Model gateway: the one interface agents use to talk to an LLM. Today the
implementations are Azure OpenAI (azure_openai.py) and a scripted fake for
tests (fake.py); another provider or an agent framework can be added
behind the same interface later without touching the agents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict

    def openai_format(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str          # JSON text, as the model produced it


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    raw_id: str | None = None

    def assistant_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.arguments}} for c in self.tool_calls]
        return msg


class ModelGateway(Protocol):
    provider: str
    model: str

    def complete(self, messages: list[dict], tools: list[ToolSpec], *, max_output_tokens: int,
                 reasoning_effort: str | None = None) -> ModelTurn: ...


class GatewayNotConfigured(RuntimeError):
    pass


def get_gateway(provider: str | None = None) -> ModelGateway | None:
    """The configured gateway, or None for provider 'none'."""
    from app.core.config import settings

    provider = (provider or settings.LLM_PROVIDER or "none").lower()
    if provider == "none":
        return None
    if provider == "azure":
        from app.agents.llm.azure_openai import AzureOpenAIGateway
        return AzureOpenAIGateway.from_settings()
    raise GatewayNotConfigured(f"Unknown LLM provider '{provider}'.")
