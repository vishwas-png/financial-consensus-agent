"""consensus_check: the guardrail that ends the Analyst <-> Critic debate.

Meaning of the counters:
- iteration_count = number of COMPLETED Analyst/Critic rounds.
- max_iterations  = the maximum number of rounds IN TOTAL.
  max_iterations=3 means at most 3 analyst runs and 3 critic runs.

After every critic step:
1. The consensus_check NODE counts the round that just finished (iteration_count + 1)
   and sets forced_exit=True if the cap was reached while the critic still disagrees.
2. The ROUTER `route_after_consensus` reads that updated state and returns:
   - "done"     if the critic agrees, or the cap is reached (forced exit);
   - "continue" otherwise (loop back to the analyst for a revision).

It is split into a node + router because LangGraph routing functions can't update state.
"""

from typing import Any, Literal

from src.core.state import GraphState

Decision = Literal["done", "continue"]


def critic_agrees(state: GraphState) -> bool:
    """True only if the critic has run and explicitly agrees."""
    return state.critic_feedback is not None and state.critic_feedback.agrees


def consensus_check(state: GraphState) -> dict[str, Any]:
    """Node: record the finished round and flag a forced exit."""
    rounds_completed = state.iteration_count + 1
    cap_reached = rounds_completed >= state.max_iterations
    return {
        "iteration_count": rounds_completed,
        "forced_exit": cap_reached and not critic_agrees(state),
    }


def route_after_consensus(state: GraphState) -> Decision:
    """Router: decide whether the debate is over. Pure function of the state."""
    if critic_agrees(state):
        return "done"
    if state.iteration_count >= state.max_iterations:
        return "done"  # forced exit: the cap is reached and they still disagree
    return "continue"
