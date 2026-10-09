"""critic node: reviews the analyst's thesis and returns CriticFeedback."""

from collections.abc import Callable
from typing import Any

from src.clients.groq_client import GroqClient
from src.config import REASONING_MODEL
from src.core.nodes.context import evidence_block
from src.core.prompts import CRITIC_PROMPT
from src.core.schemas import CriticFeedback
from src.core.state import GraphState
from src.core.structured_output import call_with_schema

CRITIC_TOOL_NAME = "submit_critique"
CRITIC_TOOL_DESCRIPTION = "Submit your review of the analyst's thesis."


def build_critic_messages(state: GraphState) -> list[dict[str, Any]]:
    """System prompt + the same evidence the analyst saw + the analyst's output."""
    if state.analyst_output is None:
        raise ValueError("critic node ran before the analyst produced an output")
    content = (
        f"{evidence_block(state)}\n\n"
        f"Analyst's output (round {state.iteration_count + 1}):\n"
        f"{state.analyst_output.model_dump_json(indent=1)}"
    )
    return [
        {"role": "system", "content": CRITIC_PROMPT},
        {"role": "user", "content": content},
    ]


def make_critic_node(client: GroqClient) -> Callable[[GraphState], dict[str, Any]]:
    def critic(state: GraphState) -> dict[str, Any]:
        feedback = call_with_schema(
            client,
            build_critic_messages(state),
            schema=CriticFeedback,
            tool_name=CRITIC_TOOL_NAME,
            description=CRITIC_TOOL_DESCRIPTION,
            model=REASONING_MODEL,
        )
        return {"critic_feedback": feedback}

    return critic
