"""
Tool registry for the agent loop: name + JSON schema + handler. Handlers
take the parsed arguments and return JSON-serialisable data; the registry
turns that into the text sent back to the model, capped in size.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from app.agents.llm.gateway import ToolSpec

MAX_RESULT_CHARS = 12_000


class ToolInputError(ValueError):
    """Raised by a handler for bad arguments; the message goes back to the model."""


@dataclass
class _Tool:
    spec: ToolSpec
    handler: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, _Tool] = {}

    def register(self, name: str, description: str, parameters: dict, handler: Callable[..., Any]) -> None:
        params = {"type": "object", "properties": {}, "additionalProperties": False, **parameters}
        self._tools[name] = _Tool(ToolSpec(name, description, params), handler)

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def dispatch(self, name: str, arguments: str) -> tuple[str, bool]:
        """Returns (result text, ok)."""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool '{name}'", "tools": self.names()}), False
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ToolInputError("arguments must be a JSON object")
            result = tool.handler(**args)
            ok = True
        except (ToolInputError, TypeError, ValueError, KeyError) as exc:
            result, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + ' ... [truncated - narrow the request (offset/limit/filter)]'
        return text, ok
