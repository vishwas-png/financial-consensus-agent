"""Guardrail tests: the debate always ends, and a forced exit is flagged.

Semantics under test: max_iterations = maximum Analyst rounds in total.
"""

import pytest
from langgraph.errors import GraphRecursionError

from src.core.graph import run_research, stream_research
from src.core.nodes import consensus_check as guardrail_module
from src.core.nodes.analyst import ANALYST_TOOL_NAME
from src.core.nodes.consensus_check import consensus_check, route_after_consensus
from src.core.nodes.critic import CRITIC_TOOL_NAME
from src.core.schemas import AnalystOutput, CriticFeedback
from src.core.state import GraphState
from src.tools.market_data import InvalidTickerError
from tests.fakes import ToolRoutingClient
from tests.test_market_data import FakeTicker

AGREE = CriticFeedback(agrees=True, objections=[], missing_context=[])
DISAGREE = CriticFeedback(agrees=False, objections=["weak"], missing_context=[])


def after_round(iteration_count: int, max_iterations: int, feedback: CriticFeedback | None) -> GraphState:
    """State as it looks after the consensus_check node has run (node update applied)."""
    state = GraphState(ticker="AAPL", iteration_count=iteration_count,
                       max_iterations=max_iterations, critic_feedback=feedback)
    return state.model_copy(update=consensus_check(state))


# ---------- Node + router, step by step ----------

def test_agreement_in_first_round_ends_without_force() -> None:
    state = after_round(0, 3, AGREE)
    assert state.iteration_count == 1
    assert state.forced_exit is False
    assert route_after_consensus(state) == "done"


def test_disagreement_below_cap_continues_and_counts_round() -> None:
    state = after_round(0, 3, DISAGREE)
    assert state.iteration_count == 1
    assert state.forced_exit is False
    assert route_after_consensus(state) == "continue"


def test_disagreement_in_second_of_three_rounds_continues() -> None:
    state = after_round(1, 3, DISAGREE)
    assert state.iteration_count == 2
    assert route_after_consensus(state) == "continue"


def test_disagreement_in_last_round_forces_exit() -> None:
    state = after_round(2, 3, DISAGREE)  # third round just finished
    assert state.iteration_count == 3
    assert state.forced_exit is True
    assert route_after_consensus(state) == "done"


def test_agreement_in_last_round_is_consensus_not_forced() -> None:
    state = after_round(2, 3, AGREE)
    assert state.iteration_count == 3
    assert state.forced_exit is False
    assert route_after_consensus(state) == "done"


def test_max_iterations_one_means_single_round() -> None:
    state = after_round(0, 1, DISAGREE)
    assert state.iteration_count == 1
    assert state.forced_exit is True
    assert route_after_consensus(state) == "done"


def test_missing_critic_feedback_counts_as_disagreement() -> None:
    state = after_round(0, 3, None)
    assert route_after_consensus(state) == "continue"
    assert after_round(2, 3, None).forced_exit is True


@pytest.mark.parametrize("max_iterations", [1, 2, 3, 5, 10])
def test_router_never_continues_past_cap(max_iterations: int) -> None:
    """Simulate a critic that never agrees: the loop must stop after exactly max_iterations rounds."""
    state = GraphState(ticker="AAPL", max_iterations=max_iterations, critic_feedback=DISAGREE)
    rounds = 0
    while True:
        rounds += 1  # analyst + critic ran
        state = state.model_copy(update=consensus_check(state))
        if route_after_consensus(state) == "done":
            break
        assert rounds < 100, "guardrail failed: loop did not stop"
    assert rounds == max_iterations
    assert state.iteration_count == max_iterations
    assert state.forced_exit is True


# ---------- Whole graph (fake LLM, fake yfinance) ----------

def no_filings(ticker: str) -> list[str]:
    """Filing retriever stub: these tests are about the loop, not RAG."""
    return []


def analysis(round_number: int) -> AnalystOutput:
    return AnalystOutput(thesis=f"Thesis v{round_number}", supporting_data_points=["x"],
                         reasoning="r", confidence="medium")


def debate_client(critic_agrees_in_round: int | None) -> ToolRoutingClient:
    """Analyst writes version N in round N; critic agrees only in the given round (None = never)."""
    return ToolRoutingClient({
        ANALYST_TOOL_NAME: analysis,
        CRITIC_TOOL_NAME: lambda n: AGREE if n == critic_agrees_in_round else DISAGREE,
    })


def run(client: ToolRoutingClient, max_iterations: int) -> GraphState:
    return run_research("AAPL", max_iterations=max_iterations, client=client,
                        ticker_factory=lambda s: FakeTicker(), filing_retriever=no_filings)


def test_graph_forced_exit_after_exactly_max_iterations_rounds() -> None:
    client = debate_client(critic_agrees_in_round=None)
    final = run(client, max_iterations=3)
    assert len(client.calls_for(ANALYST_TOOL_NAME)) == 3
    assert len(client.calls_for(CRITIC_TOOL_NAME)) == 3
    assert final.iteration_count == 3
    assert final.forced_exit is True
    assert final.critic_feedback == DISAGREE
    assert final.analyst_output is not None and final.analyst_output.thesis == "Thesis v3"


def test_graph_stops_early_on_consensus_in_round_two() -> None:
    client = debate_client(critic_agrees_in_round=2)
    final = run(client, max_iterations=3)
    assert len(client.calls_for(ANALYST_TOOL_NAME)) == 2
    assert final.iteration_count == 2
    assert final.forced_exit is False
    assert final.critic_feedback == AGREE


def test_graph_single_round_on_immediate_consensus() -> None:
    client = debate_client(critic_agrees_in_round=1)
    final = run(client, max_iterations=3)
    assert len(client.calls) == 2  # one analyst + one critic call
    assert final.iteration_count == 1
    assert final.forced_exit is False


def test_graph_consensus_in_final_round_is_not_forced() -> None:
    final = run(debate_client(critic_agrees_in_round=3), max_iterations=3)
    assert final.iteration_count == 3
    assert final.forced_exit is False


def test_graph_revision_round_sees_previous_critique() -> None:
    client = debate_client(critic_agrees_in_round=2)
    run(client, max_iterations=3)
    first, second = client.calls_for(ANALYST_TOOL_NAME)
    assert "REVISION" not in first["messages"][1]["content"]
    assert "REVISION" in second["messages"][1]["content"]
    assert "Thesis v1" in second["messages"][1]["content"]


def test_graph_large_cap_terminates_without_recursion_error() -> None:
    client = debate_client(critic_agrees_in_round=None)
    final = run(client, max_iterations=10)
    assert final.iteration_count == 10
    assert len(client.calls_for(ANALYST_TOOL_NAME)) == 10
    assert final.forced_exit is True


def test_recursion_limit_backstop_catches_a_broken_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the router were buggy and always said 'continue', LangGraph must still stop the run."""
    monkeypatch.setattr(guardrail_module, "route_after_consensus", lambda state: "continue")
    client = debate_client(critic_agrees_in_round=None)
    with pytest.raises(GraphRecursionError):
        run(client, max_iterations=3)
    # Stopped within one extra round of the intended cap, not after thousands of LLM calls
    assert len(client.calls_for(ANALYST_TOOL_NAME)) <= 4


def test_stream_yields_node_updates_in_order() -> None:
    updates = list(stream_research("AAPL", max_iterations=2, client=debate_client(None),
                                   ticker_factory=lambda s: FakeTicker(), filing_retriever=no_filings))
    assert [name for name, _ in updates] == [
        "fetch_data", "analyst", "critic", "consensus_check",
        "analyst", "critic", "consensus_check",
    ]
    assert updates[-1][1] == {"iteration_count": 2, "forced_exit": True}


def test_invalid_ticker_rejected_before_any_llm_call() -> None:
    client = debate_client(critic_agrees_in_round=1)
    with pytest.raises(InvalidTickerError):
        run_research("RELIANCE.NS", client=client, ticker_factory=lambda s: FakeTicker(), filing_retriever=no_filings)
    assert client.calls == []


# ---------- Live (opt-in: pytest -m live) ----------

@pytest.mark.live
def test_live_single_round_debate() -> None:
    final = run_research("AAPL", max_iterations=1)
    assert final.analyst_output is not None
    assert final.critic_feedback is not None
    assert final.iteration_count == 1
    assert final.forced_exit is (not final.critic_feedback.agrees)
    # Stage 4: real 10-K excerpts were retrieved, and every citation points at one of them
    assert final.filing_chunks
    provided_ids = {chunk.split("]")[0].lstrip("[") for chunk in final.filing_chunks}
    assert set(final.analyst_output.filing_citations) <= provided_ids
