"""
Scripted model gateway for tests: replays a fixed list of turns, or asks a
callable for each turn (so a test can react to tool results).
"""
from __future__ import annotations

import json
from typing import Callable

from app.agents.llm.gateway import ModelTurn, ToolCall, ToolSpec, Usage


class FakeGateway:
    provider = "fake"
    model = "fake-model"

    def __init__(self, script: list[dict] | Callable[[list[dict]], dict]) -> None:
        self._script = script
        self._i = 0
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[ToolSpec], *, max_output_tokens: int,
                 reasoning_effort: str | None = None) -> ModelTurn:
        self.calls.append([dict(m) for m in messages])
        if callable(self._script):
            turn = self._script(messages)
        elif self._i < len(self._script):
            turn = self._script[self._i]
        else:
            turn = {"text": "done"}
        self._i += 1
        calls = [ToolCall(id=f"call_{self._i}_{n}", name=c["name"],
                          arguments=c["arguments"] if isinstance(c["arguments"], str) else json.dumps(c["arguments"]))
                 for n, c in enumerate(turn.get("tool_calls", []))]
        return ModelTurn(text=turn.get("text"), tool_calls=calls,
                         usage=Usage(**turn.get("usage", {"prompt_tokens": 100, "completion_tokens": 20,
                                                          "total_tokens": 120})),
                         finish_reason="tool_calls" if calls else "stop", raw_id=f"fake-{self._i}")
