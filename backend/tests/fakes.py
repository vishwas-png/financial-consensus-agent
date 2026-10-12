"""Shared test doubles for LLM-driven code and embeddings."""

import hashlib
import json
import re
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


class BagOfWordsEmbedder:
    """Deterministic fake embedder: hashed word counts. Texts sharing words are 'similar'.

    Good enough to test topic-change detection and vector search without
    downloading a real embedding model.
    """

    DIMENSIONS = 256

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        vectors = []
        for text in texts:
            vector = [0.0] * self.DIMENSIONS
            for word in re.findall(r"[a-z]{4,}", text.lower()):
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIMENSIONS] += 1.0
            if not any(vector):
                vector[0] = 1.0  # avoid all-zero vectors (undefined cosine)
            vectors.append(vector)
        return vectors


def low_risk_assessment(call_number: int = 1) -> dict[str, Any]:
    """A valid Risk Officer answer that cites no excerpts (works with or without filings)."""
    return {"key_risks": [{"risk": "Valuation multiple leaves little room for error.", "severity": "medium",
                           "citation": None}],
            "what_would_change_the_thesis": ["trailing_pe falls below 20"], "overall_risk": "medium"}
