"""Builds and runs the research graph.

    START -> fetch_data -> analyst -> critic -> consensus_check
                              ^                       |
                              +------ "continue" -----+
                                                      | "done" (consensus OR forced exit)
                                                      v
                                                   auditor -> END

fetch_data loads market data (yfinance) and SEC filing excerpts (RAG over the latest 10-K).
The auditor (LLM-as-a-Judge) checks the final analysis against that evidence.

`core/` knows nothing about users: it takes a ticker and settings, returns a GraphState.

Usage (manual check):  python -m src.core.graph AAPL [max_iterations]
"""

import json
import sys
from collections.abc import Callable, Iterator
from typing import Any

import yfinance as yf
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.clients.groq_client import GroqClient
from src.config import MAX_ITERATIONS
from src.core.nodes import consensus_check as guardrail
from src.core.nodes.analyst import make_analyst_node
from src.core.nodes.auditor import make_auditor_node
from src.core.nodes.critic import make_critic_node
from src.core.nodes.fetch_data import FilingRetriever, default_filing_retriever, make_fetch_data_node
from src.core.schemas import MarketDataToolInput
from src.core.state import GraphState
from src.tools.market_data import InvalidTickerError

# Graph steps per debate round: analyst, critic, consensus_check
STEPS_PER_ROUND = 3
# Steps outside the loop: fetch_data before it, auditor after it
STEPS_OUTSIDE_LOOP = 2


def build_graph(
    client: GroqClient,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
) -> Any:
    """Wire the nodes together and compile the graph."""
    graph = StateGraph(GraphState)
    graph.add_node("fetch_data", make_fetch_data_node(ticker_factory, filing_retriever))
    graph.add_node("analyst", make_analyst_node(client))
    graph.add_node("critic", make_critic_node(client))
    graph.add_node("consensus_check", guardrail.consensus_check)
    graph.add_node("auditor", make_auditor_node(client))

    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "analyst")
    graph.add_edge("analyst", "critic")
    graph.add_edge("critic", "consensus_check")
    graph.add_conditional_edges(
        "consensus_check",
        guardrail.route_after_consensus,
        {"continue": "analyst", "done": "auditor"},
    )
    graph.add_edge("auditor", END)
    return graph.compile()


def recursion_limit_for(max_iterations: int) -> int:
    """Hard step limit for LangGraph: a second safety net behind consensus_check.

    A correct run needs fetch_data + 3 steps per round + auditor. If the guardrail were ever
    broken, LangGraph raises GraphRecursionError instead of looping (and paying) forever.
    LangGraph's own default (10007 steps in v1.2) is far too high to protect us.
    """
    return STEPS_OUTSIDE_LOOP + STEPS_PER_ROUND * max_iterations + 1


def _initial_state(ticker: str, max_iterations: int) -> GraphState:
    """Validate the ticker before the graph starts, so a bad ticker costs no LLM calls."""
    try:
        symbol = MarketDataToolInput(ticker=ticker).ticker
    except ValidationError as error:
        raise InvalidTickerError(f"Invalid ticker {ticker!r}: {error.errors()[0]['msg']}") from error
    return GraphState(ticker=symbol, max_iterations=max_iterations)


def run_research(
    ticker: str,
    max_iterations: int = MAX_ITERATIONS,
    client: GroqClient | None = None,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
) -> GraphState:
    """Run the whole debate for one ticker and return the final state."""
    app = build_graph(client or GroqClient(), ticker_factory, filing_retriever)
    result = app.invoke(
        _initial_state(ticker, max_iterations),
        config={"recursion_limit": recursion_limit_for(max_iterations)},
    )
    return GraphState.model_validate(result)


def stream_research(
    ticker: str,
    max_iterations: int = MAX_ITERATIONS,
    client: GroqClient | None = None,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Run the debate and yield (node_name, state_update) after each step (for live streaming)."""
    app = build_graph(client or GroqClient(), ticker_factory, filing_retriever)
    for chunk in app.stream(
        _initial_state(ticker, max_iterations),
        config={"recursion_limit": recursion_limit_for(max_iterations)},
        stream_mode="updates",
    ):
        for node_name, update in chunk.items():
            yield node_name, update


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python -m src.core.graph <TICKER> [max_iterations]")
        sys.exit(2)
    # LLM text often has non-ASCII dashes; don't crash on the Windows console's cp1252 encoding
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    max_iter = int(sys.argv[2]) if len(sys.argv) == 3 else MAX_ITERATIONS
    rounds = 0
    forced = False
    for node, update in stream_research(sys.argv[1], max_iterations=max_iter):
        if node == "fetch_data":
            data = update["raw_data"]
            print(f"[fetch_data] {data['profile']['name']} @ {data['price']['current']}; "
                  f"{len(update['filing_chunks'])} filing excerpts")
        elif node == "analyst":
            out = update["analyst_output"]
            print(f"\n=== Round {rounds + 1} ===\n[analyst] ({out.confidence}) {out.thesis}")
            print(f"   citations: {out.filing_citations}")
        elif node == "critic":
            feedback = update["critic_feedback"]
            print(f"[critic] agrees={feedback.agrees}")
            for objection in feedback.objections:
                print(f"   - {objection}")
        elif node == "consensus_check":
            rounds = update["iteration_count"]
            forced = update["forced_exit"]
        elif node == "auditor":
            verdict = update["auditor_verdict"]
            print(f"\n[auditor] is_grounded={verdict.is_grounded}: {verdict.notes}")
            for claim in verdict.unsupported_claims:
                print(f"   ! {claim}")
    outcome = "UNRESOLVED DISAGREEMENT (forced exit)" if forced else "CONSENSUS"
    print(f"\n{json.dumps({'outcome': outcome, 'rounds': rounds, 'max_iterations': max_iter})}")
