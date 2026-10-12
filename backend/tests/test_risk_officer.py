"""Risk Officer node (Stage 9): schema, prompt contents, citation validation with repair."""

import pytest
from pydantic import ValidationError

from src.config import REASONING_MODEL
from src.core.nodes.risk_officer import (
    RISK_OFFICER_TOOL_NAME,
    build_risk_officer_messages,
    make_risk_officer_node,
)
from src.core.prompts import RISK_OFFICER_PROMPT
from src.core.schemas import CriticFeedback, RiskAssessment
from src.core.structured_output import ToolCallError
from tests.fakes import ToolRoutingClient, low_risk_assessment
from tests.test_auditor import CHUNKS, state


def assessment(citation: str | None) -> dict[str, object]:
    return {"key_risks": [{"risk": "Supplier concentration could disrupt iPhone supply.", "severity": "high",
                           "citation": citation}],
            "what_would_change_the_thesis": ["services growth stalls"], "overall_risk": "high"}


# ---------- Schema ----------

def test_schema_accepts_a_valid_assessment() -> None:
    parsed = RiskAssessment.model_validate(assessment("AAPL-10K-2025-1A-003"))
    assert parsed.key_risks[0].severity == "high" and parsed.overall_risk == "high"


@pytest.mark.parametrize("change", [
    {"key_risks": []},                                    # at least one risk
    {"key_risks": [assessment(None)["key_risks"][0]] * 6},  # at most five
    {"what_would_change_the_thesis": []},
    {"overall_risk": "extreme"},
    {"extra_field": 1},
])
def test_schema_rejects_bad_assessments(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RiskAssessment.model_validate(assessment(None) | change)


# ---------- Prompt ----------

def test_prompt_has_evidence_final_analysis_and_unresolved_objections() -> None:
    forced = state(forced_exit=True, iteration_count=3)
    system, user = build_risk_officer_messages(forced)
    assert system["content"] == RISK_OFFICER_PROMPT
    assert all(chunk in user["content"] for chunk in CHUNKS)
    assert '"trailing_pe":39.0' in user["content"]
    assert "Apple is fairly valued." in user["content"]
    assert "NO consensus after 3 round(s)" in user["content"]
    assert "CRITIC-SECRET-OBJECTION" in user["content"]  # unresolved objections are risks too


def test_prompt_after_consensus_does_not_list_objections() -> None:
    agreed = state(forced_exit=False, iteration_count=2,
                   critic_feedback=CriticFeedback(agrees=True, objections=[], missing_context=[]))
    content = build_risk_officer_messages(agreed)[1]["content"]
    assert "the critic agreed after 2 round(s)" in content


def test_runs_only_after_the_analyst() -> None:
    with pytest.raises(ValueError, match="before the analyst"):
        build_risk_officer_messages(state(analyst_output=None))


# ---------- Node ----------

def test_node_returns_assessment_using_the_reasoning_model() -> None:
    client = ToolRoutingClient({RISK_OFFICER_TOOL_NAME: lambda n: assessment("AAPL-10K-2025-1A-003")})
    update = make_risk_officer_node(client)(state())
    assert set(update) == {"risk_assessment"}
    assert update["risk_assessment"].key_risks[0].citation == "AAPL-10K-2025-1A-003"
    assert client.calls[0]["model"] == REASONING_MODEL


def test_market_data_risks_may_cite_nothing() -> None:
    client = ToolRoutingClient({RISK_OFFICER_TOOL_NAME: low_risk_assessment})
    assert make_risk_officer_node(client)(state())["risk_assessment"].key_risks[0].citation is None


def test_invented_citation_is_repaired() -> None:
    client = ToolRoutingClient({RISK_OFFICER_TOOL_NAME: lambda n: assessment("AAPL-10K-2025-1A-999" if n == 1
                                                                            else "AAPL-10K-2025-1A-003")})
    update = make_risk_officer_node(client)(state())
    assert update["risk_assessment"].key_risks[0].citation == "AAPL-10K-2025-1A-003"
    repair = client.calls[1]["messages"][-1]["content"]
    assert "AAPL-10K-2025-1A-999" in repair and "AAPL-10K-2025-1A-003" in repair  # names valid ids


def test_invented_citation_twice_fails() -> None:
    client = ToolRoutingClient({RISK_OFFICER_TOOL_NAME: lambda n: assessment("MADE-UP-ID")})
    with pytest.raises(ToolCallError):
        make_risk_officer_node(client)(state())


# ---------- Live (opt-in) ----------

@pytest.mark.live
def test_live_risk_officer_cites_real_excerpts() -> None:
    from src.clients.groq_client import GroqClient

    result = make_risk_officer_node(GroqClient())(state(forced_exit=True, iteration_count=1))["risk_assessment"]
    valid = {"AAPL-10K-2025-1A-003", "AAPL-10K-2025-7-001"}
    assert all(item.citation in valid or item.citation is None for item in result.key_risks)
