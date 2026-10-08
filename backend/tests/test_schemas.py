"""Schema validation tests: good LLM output passes, malformed output is rejected."""

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    AnalystOutput,
    AuditorVerdict,
    CriticFeedback,
    MarketDataToolInput,
)
from src.core.state import GraphState


def valid_analyst_payload() -> dict:
    return {
        "thesis": "AAPL is fairly valued.",
        "supporting_data_points": ["P/E of 28", "Revenue growth 5% YoY"],
        "reasoning": "Growth is slowing but margins are stable.",
        "confidence": "medium",
    }


# ---------- AnalystOutput ----------

def test_analyst_output_valid_and_citations_default_empty() -> None:
    output = AnalystOutput(**valid_analyst_payload())
    assert output.confidence == "medium"
    assert output.filing_citations == []


def test_analyst_output_rejects_unknown_confidence() -> None:
    payload = valid_analyst_payload() | {"confidence": "very high"}
    with pytest.raises(ValidationError):
        AnalystOutput(**payload)


def test_analyst_output_rejects_missing_field() -> None:
    payload = valid_analyst_payload()
    del payload["thesis"]
    with pytest.raises(ValidationError):
        AnalystOutput(**payload)


def test_analyst_output_rejects_extra_field() -> None:
    payload = valid_analyst_payload() | {"price_target": 250}
    with pytest.raises(ValidationError):
        AnalystOutput(**payload)


def test_analyst_output_rejects_wrong_type() -> None:
    payload = valid_analyst_payload() | {"supporting_data_points": "not a list"}
    with pytest.raises(ValidationError):
        AnalystOutput(**payload)


# ---------- CriticFeedback ----------

def test_critic_feedback_valid() -> None:
    feedback = CriticFeedback(agrees=False, objections=["Ignores debt"], missing_context=[])
    assert feedback.agrees is False


def test_critic_feedback_rejects_missing_agrees() -> None:
    with pytest.raises(ValidationError):
        CriticFeedback(objections=[], missing_context=[])


# ---------- AuditorVerdict ----------

def test_auditor_verdict_valid() -> None:
    verdict = AuditorVerdict(is_grounded=True, unsupported_claims=[], notes="All claims cited.")
    assert verdict.is_grounded is True


def test_auditor_verdict_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        AuditorVerdict(is_grounded=True, unsupported_claims=[], notes="", score=0.9)


# ---------- MarketDataToolInput (ticker validation) ----------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AAPL", "AAPL"), ("aapl", "AAPL"), ("  msft ", "MSFT"), ("BRK-B", "BRK-B"), ("brk-b", "BRK-B")],
)
def test_ticker_valid_and_normalized(raw: str, expected: str) -> None:
    assert MarketDataToolInput(ticker=raw).ticker == expected


@pytest.mark.parametrize("bad", ["", "   ", "RELIANCE.NS", "VOD.L", "TOOLONG", "AAP1", "BRK-BB", "-B"])
def test_ticker_invalid_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        MarketDataToolInput(ticker=bad)


def test_ticker_rejects_non_string() -> None:
    with pytest.raises(ValidationError):
        MarketDataToolInput(ticker=123)


# ---------- GraphState ----------

def test_graph_state_defaults() -> None:
    state = GraphState(ticker="AAPL")
    assert state.iteration_count == 0
    assert state.max_iterations == 3
    assert state.raw_data == {}
    assert state.filing_chunks == []
    assert state.analyst_output is None
    assert state.critic_feedback is None
    assert state.auditor_verdict is None


def test_graph_state_rejects_zero_max_iterations() -> None:
    with pytest.raises(ValidationError):
        GraphState(ticker="AAPL", max_iterations=0)


def test_graph_state_rejects_negative_iteration_count() -> None:
    with pytest.raises(ValidationError):
        GraphState(ticker="AAPL", iteration_count=-1)


def test_graph_state_accepts_nested_agent_outputs() -> None:
    state = GraphState(
        ticker="AAPL",
        analyst_output=valid_analyst_payload(),
        critic_feedback={"agrees": True, "objections": [], "missing_context": []},
    )
    assert isinstance(state.analyst_output, AnalystOutput)
    assert state.critic_feedback is not None and state.critic_feedback.agrees is True


def test_graph_state_defaults_not_shared_between_instances() -> None:
    first = GraphState(ticker="AAPL")
    second = GraphState(ticker="MSFT")
    first.filing_chunks.append("chunk")
    assert second.filing_chunks == []
