"""auditor node (LLM-as-a-Judge): checks the final analysis against the evidence.

Runs after every debate, whether it ended in consensus or a forced exit. It sees the
final AnalystOutput and the same evidence the agents had, but NOT the critic's
feedback, so its verdict is an independent check rather than a third debate voice.
"""

from collections.abc import Callable
from typing import Any

from src.clients.groq_client import GroqClient
from src.config import REASONING_MODEL
from src.core.nodes.context import evidence_block
from src.core.prompts import AUDITOR_PROMPT
from src.core.schemas import AuditorVerdict
from src.core.state import GraphState
from src.core.structured_output import call_with_schema

AUDITOR_TOOL_NAME = "submit_verdict"
AUDITOR_TOOL_DESCRIPTION = "Submit your grounding verdict on the analyst's final analysis."


def build_auditor_messages(state: GraphState) -> list[dict[str, Any]]:
    """System prompt + evidence + the final analysis (with its citations listed)."""
    if state.analyst_output is None:
        raise ValueError("auditor node ran before the analyst produced an output")
    cited = state.analyst_output.filing_citations
    content = (
        f"{evidence_block(state)}\n\n"
        f"Excerpt ids the analyst cited: {', '.join(cited) if cited else 'none'}\n\n"
        f"Analyst's final analysis:\n{state.analyst_output.model_dump_json(indent=1)}"
    )
    return [
        {"role": "system", "content": AUDITOR_PROMPT},
        {"role": "user", "content": content},
    ]


def check_verdict_consistency(verdict: AuditorVerdict) -> None:
    """is_grounded and unsupported_claims must agree; otherwise the verdict is ambiguous."""
    if verdict.is_grounded and verdict.unsupported_claims:
        raise ValueError("is_grounded is true but unsupported_claims is not empty; set is_grounded to false "
                         "or remove the claims")
    if not verdict.is_grounded and not verdict.unsupported_claims:
        raise ValueError("is_grounded is false but unsupported_claims is empty; list each unsupported claim")


def make_auditor_node(client: GroqClient) -> Callable[[GraphState], dict[str, Any]]:
    def auditor(state: GraphState) -> dict[str, Any]:
        verdict = call_with_schema(
            client,
            build_auditor_messages(state),
            schema=AuditorVerdict,
            tool_name=AUDITOR_TOOL_NAME,
            description=AUDITOR_TOOL_DESCRIPTION,
            model=REASONING_MODEL,
            extra_check=check_verdict_consistency,
        )
        return {"auditor_verdict": verdict}

    return auditor
