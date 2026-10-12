"""Auditor node tests: independent prompt, verdict consistency, state update."""

import pytest

from src.config import REASONING_MODEL
from src.core.nodes.auditor import (
    AUDITOR_TOOL_NAME,
    build_auditor_messages,
    check_verdict_consistency,
    make_auditor_node,
)
from src.core.schemas import AnalystOutput, AuditorVerdict, CriticFeedback
from src.core.state import GraphState
from src.core.structured_output import ToolCallError
from tests.fakes import ToolRoutingClient

CHUNKS = [
    "[AAPL-10K-2025-1A-003] (Item 1A - Risk Factors) The Company depends on single-source suppliers.",
    "[AAPL-10K-2025-7-001] (Item 7 - Management's Discussion and Analysis) Services net sales grew.",
]
ANALYSIS = AnalystOutput(
    thesis="Apple is fairly valued.",
    supporting_data_points=["trailing_pe = 39.0"],
    reasoning="Services growth offsets supplier concentration risk.",
    confidence="medium",
    filing_citations=["AAPL-10K-2025-7-001"],
)
CRITIC = CriticFeedback(agrees=False, objections=["CRITIC-SECRET-OBJECTION"], missing_context=[])
GROUNDED = AuditorVerdict(is_grounded=True, unsupported_claims=[], notes="All claims supported.")
UNGROUNDED = AuditorVerdict(is_grounded=False, unsupported_claims=["P/E of 45 -- data says 39.0"], notes="One wrong number.")


def state(**overrides: object) -> GraphState:
    base = dict(ticker="AAPL", raw_data={"valuation": {"trailing_pe": 39.0}}, filing_chunks=CHUNKS,
                analyst_output=ANALYSIS, critic_feedback=CRITIC)
    return GraphState(**(base | overrides))


# ---------- Prompt ----------

def test_prompt_has_evidence_analysis_and_cited_ids() -> None:
    content = build_auditor_messages(state())[1]["content"]
    assert '"trailing_pe":39.0' in content
    assert all(chunk in content for chunk in CHUNKS)
    assert "Excerpt ids the analyst cited: AAPL-10K-2025-7-001" in content
    assert "Apple is fairly valued." in content


def test_prompt_excludes_critic_feedback() -> None:
    content = build_auditor_messages(state())[1]["content"]
    assert "CRITIC-SECRET-OBJECTION" not in content


def test_prompt_says_none_when_nothing_cited() -> None:
    no_citations = ANALYSIS.model_copy(update={"filing_citations": []})
    assert "cited: none" in build_auditor_messages(state(analyst_output=no_citations))[1]["content"]


def test_requires_analyst_output() -> None:
    with pytest.raises(ValueError):
        build_auditor_messages(state(analyst_output=None))


# ---------- Consistency check ----------

def test_consistent_verdicts_pass() -> None:
    check_verdict_consistency(GROUNDED)
    check_verdict_consistency(UNGROUNDED)


@pytest.mark.parametrize("verdict", [
    AuditorVerdict(is_grounded=True, unsupported_claims=["x -- y"], notes=""),
    AuditorVerdict(is_grounded=False, unsupported_claims=[], notes=""),
])
def test_inconsistent_verdicts_rejected(verdict: AuditorVerdict) -> None:
    with pytest.raises(ValueError):
        check_verdict_consistency(verdict)


# ---------- Node ----------

def test_node_returns_verdict_and_data_check() -> None:
    client = ToolRoutingClient({AUDITOR_TOOL_NAME: lambda n: UNGROUNDED})
    update = make_auditor_node(client)(state())
    assert set(update) == {"auditor_verdict", "data_check"}
    assert update["auditor_verdict"] == UNGROUNDED
    assert len(update["data_check"].checks) == len(state().analyst_output.supporting_data_points)
    assert client.calls[0]["model"] == REASONING_MODEL


def test_inconsistent_verdict_repaired() -> None:
    inconsistent = AuditorVerdict(is_grounded=False, unsupported_claims=[], notes="hmm")
    client = ToolRoutingClient({AUDITOR_TOOL_NAME: lambda n: inconsistent if n == 1 else GROUNDED})
    update = make_auditor_node(client)(state())
    assert update["auditor_verdict"] == GROUNDED
    assert len(client.calls) == 2
    assert "unsupported_claims is empty" in client.calls[1]["messages"][-1]["content"]


def test_inconsistent_twice_raises() -> None:
    inconsistent = AuditorVerdict(is_grounded=True, unsupported_claims=["x -- y"], notes="")
    client = ToolRoutingClient({AUDITOR_TOOL_NAME: lambda n: inconsistent})
    with pytest.raises(ToolCallError):
        make_auditor_node(client)(state())
