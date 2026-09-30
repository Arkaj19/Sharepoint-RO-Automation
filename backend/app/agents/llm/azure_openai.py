"""
Azure OpenAI implementation of the model gateway (chat completions with
tool calling). GPT-5 deployments are reasoning models: no `temperature`,
output is capped with `max_completion_tokens`, and `reasoning_effort` is
sent when configured (dropped automatically if the API version rejects it).
"""
from __future__ import annotations

from openai import AzureOpenAI, BadRequestError

from app.agents.llm.gateway import GatewayNotConfigured, ModelTurn, ToolCall, ToolSpec, Usage
from app.core.config import settings


class AzureOpenAIGateway:
    provider = "azure"

    def __init__(self, *, api_key: str, endpoint: str, api_version: str, deployment: str) -> None:
        self.model = deployment
        self._client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version,
                                   timeout=300, max_retries=3)
        self._send_effort = True

    @classmethod
    def from_settings(cls) -> "AzureOpenAIGateway":
        missing = [n for n in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION",
                               "AZURE_OPENAI_CHAT_DEPLOYMENT") if not getattr(settings, n)]
        if missing:
            raise GatewayNotConfigured(f"Azure OpenAI is not configured (missing {', '.join(missing)}).")
        return cls(api_key=settings.AZURE_OPENAI_API_KEY, endpoint=settings.AZURE_OPENAI_ENDPOINT,
                   api_version=settings.AZURE_OPENAI_API_VERSION, deployment=settings.AZURE_OPENAI_CHAT_DEPLOYMENT)

    def complete(self, messages: list[dict], tools: list[ToolSpec], *, max_output_tokens: int,
                 reasoning_effort: str | None = None) -> ModelTurn:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_output_tokens,
        }
        if tools:
            kwargs["tools"] = [t.openai_format() for t in tools]
            kwargs["tool_choice"] = "auto"
        if reasoning_effort and self._send_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            if "reasoning_effort" in kwargs and "reasoning_effort" in str(exc):
                self._send_effort = False
                kwargs.pop("reasoning_effort")
                resp = self._client.chat.completions.create(**kwargs)
            else:
                raise
        choice = resp.choices[0]
        msg = choice.message
        calls = [ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
                 for c in (msg.tool_calls or []) if getattr(c, "function", None)]
        u = resp.usage
        usage = Usage(prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                      completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                      total_tokens=getattr(u, "total_tokens", 0) or 0) if u else Usage()
        return ModelTurn(text=msg.content, tool_calls=calls, usage=usage, finish_reason=choice.finish_reason,
                         raw_id=resp.id)
