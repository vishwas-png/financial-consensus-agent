"""Shared test doubles for LLM-driven code."""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel


def tool_call_message(arguments: str, name: str) -> SimpleNamespace:
    """An assistant message shaped like the Groq SDK's, containing one tool call."""
    call = SimpleNamespace(id="call_1", type="function", function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(content=None, tool_calls=[call])


class ToolRoutingClient:
    """Fake GroqClient that answers based on which tool is being forced.

    `responders` maps a tool name to a function(call_number) -> pydantic model (or dict).
    call_number starts at 1 for each tool, so tests can script round-by-round behaviour,
    e.g. "the critic disagrees in round 1 and agrees in round 2".
    """

    def __init__(self, responders: dict[str, Callable[[int], BaseModel | dict[str, Any]]]) -> None:
        self.responders = responders
        self.calls: list[dict[str, Any]] = []

    def calls_for(self, tool_name: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["tool"] == tool_name]

    def chat(self, messages: list[dict[str, Any]], model: str, tools: Any = None,
             tool_choice: Any = None, temperature: float = 0.0) -> Any:
        tool_name = tool_choice["function"]["name"]
        self.calls.append({"tool": tool_name, "messages": messages, "model": model})
        call_number = len(self.calls_for(tool_name))
        answer = self.responders[tool_name](call_number)
        payload = answer.model_dump() if isinstance(answer, BaseModel) else answer
        return tool_call_message(json.dumps(payload), tool_name)
