"""risk_officer node (Stage 9): names what could make the final thesis wrong.

Runs once, after the debate (consensus or forced exit) and before the auditor. It sees the
same evidence as the other agents, the final analysis, and how the debate ended (including
the critic's last objections if they were never resolved). Its citations are validated the
same way as the analyst's: only ids of excerpts we provided, with one repair attempt.
"""

from collections.abc import Callable
from typing import Any

from src.clients.groq_client import GroqClient
from src.config import REASONING_MODEL
from src.core.nodes.context import chunk_ids, evidence_block
from src.core.prompts import RISK_OFFICER_PROMPT
from src.core.schemas import RiskAssessment
from src.core.state import GraphState
from src.core.structured_output import call_with_schema

RISK_OFFICER_TOOL_NAME = "submit_risk_assessment"
RISK_OFFICER_TOOL_DESCRIPTION = "Submit the material risks to the final thesis."


def debate_summary(state: GraphState) -> str:
    """How the debate ended, so unresolved objections are visible to the risk officer."""
    if not state.forced_exit:
        return f"Debate outcome: the critic agreed after {state.iteration_count} round(s)."
    objections = state.critic_feedback.objections if state.critic_feedback else []
    listed = "\n".join(f"- {objection}" for objection in objections) or "- (none recorded)"
    return (f"Debate outcome: NO consensus after {state.iteration_count} round(s). "
            f"The critic's unresolved objections:\n{listed}")


def build_risk_officer_messages(state: GraphState) -> list[dict[str, Any]]:
    if state.analyst_output is None:
        raise ValueError("risk_officer node ran before the analyst produced an output")
    content = (
        f"{evidence_block(state)}\n\n"
        f"Analyst's final analysis:\n{state.analyst_output.model_dump_json(indent=1)}\n\n"
        f"{debate_summary(state)}"
    )
    return [
        {"role": "system", "content": RISK_OFFICER_PROMPT},
        {"role": "user", "content": content},
    ]


def make_risk_citation_check(state: GraphState) -> Callable[[RiskAssessment], None]:
    """Reject citations that are not ids of excerpts we provided (null is allowed)."""
    allowed = chunk_ids(state)

    def check(assessment: RiskAssessment) -> None:
        unknown = [item.citation for item in assessment.key_risks if item.citation and item.citation not in allowed]
        if unknown:
            hint = f"Valid ids: {', '.join(sorted(allowed))}." if allowed else "No excerpts were provided, so use null."
            raise ValueError(f"key_risks cite ids that were not provided: {unknown}. "
                             f"Use null for risks from the market data. {hint}")

    return check


def make_risk_officer_node(client: GroqClient) -> Callable[[GraphState], dict[str, Any]]:
    def risk_officer(state: GraphState) -> dict[str, Any]:
        assessment = call_with_schema(
            client,
            build_risk_officer_messages(state),
            schema=RiskAssessment,
            tool_name=RISK_OFFICER_TOOL_NAME,
            description=RISK_OFFICER_TOOL_DESCRIPTION,
            model=REASONING_MODEL,
            extra_check=make_risk_citation_check(state),
        )
        return {"risk_assessment": assessment}

    return risk_officer
