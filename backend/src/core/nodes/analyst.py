"""analyst node: writes (or revises) the investment thesis as an AnalystOutput."""

from collections.abc import Callable
from typing import Any

from src.clients.groq_client import GroqClient
from src.config import REASONING_MODEL
from src.core.nodes.context import chunk_ids, evidence_block
from src.core.prompts import ANALYST_PROMPT
from src.core.schemas import AnalystOutput
from src.core.state import GraphState
from src.core.structured_output import call_with_schema

ANALYST_TOOL_NAME = "submit_analysis"
ANALYST_TOOL_DESCRIPTION = "Submit your investment thesis for the stock."


def build_analyst_messages(state: GraphState) -> list[dict[str, Any]]:
    """System prompt + the evidence, plus the previous thesis and critique when revising."""
    content = evidence_block(state)
    if state.analyst_output is not None and state.critic_feedback is not None:
        content += (
            "\n\nThis is a REVISION. Your previous analysis:\n"
            f"{state.analyst_output.model_dump_json(indent=1)}\n\n"
            "The critic's feedback:\n"
            f"{state.critic_feedback.model_dump_json(indent=1)}\n\n"
            "Revise your analysis and address every objection and missing-context item."
        )
    return [
        {"role": "system", "content": ANALYST_PROMPT},
        {"role": "user", "content": content},
    ]


def make_citation_check(state: GraphState) -> Callable[[AnalystOutput], None]:
    """Reject citations that are not ids of excerpts we actually provided (invented citations)."""
    allowed = chunk_ids(state)

    def check(output: AnalystOutput) -> None:
        unknown = [c for c in output.filing_citations if c not in allowed]
        if unknown:
            hint = f"Valid ids: {', '.join(sorted(allowed))}." if allowed else "No excerpts were provided, so cite none."
            raise ValueError(f"filing_citations contains ids that were not provided: {unknown}. {hint}")

    return check


def make_analyst_node(client: GroqClient) -> Callable[[GraphState], dict[str, Any]]:
    def analyst(state: GraphState) -> dict[str, Any]:
        output = call_with_schema(
            client,
            build_analyst_messages(state),
            schema=AnalystOutput,
            tool_name=ANALYST_TOOL_NAME,
            description=ANALYST_TOOL_DESCRIPTION,
            model=REASONING_MODEL,
            extra_check=make_citation_check(state),
        )
        return {"analyst_output": output}

    return analyst
