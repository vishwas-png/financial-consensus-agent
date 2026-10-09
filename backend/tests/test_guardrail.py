"""Guardrail tests: the debate always ends, and a forced exit is flagged.

Semantics under test: max_iterations = maximum Analyst rounds in total.
"""

import pytest

from src.core.nodes.consensus_check import consensus_check, route_after_consensus
from src.core.schemas import CriticFeedback
from src.core.state import GraphState

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
