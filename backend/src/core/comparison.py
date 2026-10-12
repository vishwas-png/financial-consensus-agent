"""Multi-ticker comparison (Stage 9): research each ticker, then compare them side by side.

    for each ticker:  fetch_data -> analyst -> critic -> consensus_check -> risk_officer -> auditor
    then:             comparator (LLM) -> ComparisonOutput

Each ticker runs through the SAME graph as a single research run, one ticker after another
(Groq's free tier allows ~8k tokens a minute, so running them in parallel would only queue
on rate limits). Each gets 1 debate round by default, to keep a 3-ticker comparison affordable.

Like the rest of `core/`, this knows nothing about users: it takes tickers and returns states.

Usage (manual check):  python -m src.core.comparison AAPL MSFT
"""

import json
import sys
from collections.abc import Callable, Iterator
from typing import Any

import yfinance as yf

from src.clients.groq_client import GroqClient
from src.config import REASONING_MODEL
from src.core.graph import stream_research
from src.core.nodes.context import CHUNK_ID_PATTERN
from src.core.nodes.fetch_data import FilingRetriever, default_filing_retriever
from src.core.prompts import COMPARATOR_PROMPT
from src.core.schemas import ComparisonOutput
from src.core.state import GraphState
from src.core.structured_output import call_with_schema

COMPARATOR_TOOL_NAME = "submit_comparison"
COMPARATOR_TOOL_DESCRIPTION = "Submit the side-by-side comparison of the researched companies."
COMPARE_ROUNDS = 1            # debate rounds per ticker in a comparison
EXCERPT_CHARS = 400           # each cited excerpt is shortened to this many characters
# Market data the comparator sees per ticker: everything except the long price history
COMPARATOR_DATA_SECTIONS = ("profile", "price", "valuation", "financials", "annual_financials")

# One comparison event: (ticker or None for the comparator step, node name, state update)
ComparisonEvent = tuple[str | None, str, dict[str, Any]]


def cited_excerpts(state: GraphState) -> dict[str, str]:
    """Excerpts the analyst or risk officer actually cited (id -> shortened text).

    Only these go to the comparator: sending all 8 excerpts per ticker would exceed the
    free-tier token budget for 3 tickers.
    """
    cited = set(state.analyst_output.filing_citations if state.analyst_output else [])
    if state.risk_assessment:
        cited |= {item.citation for item in state.risk_assessment.key_risks if item.citation}
    excerpts = {}
    for chunk in state.filing_chunks:
        match = CHUNK_ID_PATTERN.match(chunk)
        if match and match.group(1) in cited:
            excerpts[match.group(1)] = chunk[:EXCERPT_CHARS]
    return excerpts


def ticker_brief(state: GraphState) -> dict[str, Any]:
    """Compact JSON-able summary of one ticker's research for the comparator prompt."""
    return {
        "ticker": state.ticker,
        "market_data": {key: state.raw_data.get(key) for key in COMPARATOR_DATA_SECTIONS},
        "analysis": state.analyst_output.model_dump() if state.analyst_output else None,
        "debate": "unresolved disagreement (forced exit)" if state.forced_exit
                  else f"consensus after {state.iteration_count} round(s)",
        "critic_open_objections": state.critic_feedback.objections
                                  if state.critic_feedback and state.forced_exit else [],
        "risks": state.risk_assessment.model_dump() if state.risk_assessment else None,
        "auditor": {"is_grounded": state.auditor_verdict.is_grounded,
                    "unsupported_claims": state.auditor_verdict.unsupported_claims}
                   if state.auditor_verdict else None,
        "filing_status": state.filing_status,
    }


def build_comparator_messages(states: list[GraphState]) -> list[dict[str, Any]]:
    briefs = json.dumps([ticker_brief(state) for state in states], separators=(",", ":"))
    excerpts = [text for state in states for text in cited_excerpts(state).values()]
    excerpt_block = "\n\n".join(excerpts) if excerpts else "none"
    content = (f"Companies to compare, in order: {', '.join(s.ticker for s in states)}\n\n"
               f"Research results (JSON):\n{briefs}\n\n"
               f"Cited 10-K excerpts (shortened):\n{excerpt_block}")
    return [{"role": "system", "content": COMPARATOR_PROMPT}, {"role": "user", "content": content}]


def make_comparison_check(states: list[GraphState]) -> Callable[[ComparisonOutput], None]:
    """per_ticker must cover exactly the compared tickers, in order; preferred must be one of them;
    citations must be ids that were shown."""
    tickers = [state.ticker for state in states]
    shown_ids = {excerpt_id for state in states for excerpt_id in cited_excerpts(state)}

    def check(output: ComparisonOutput) -> None:
        listed = [entry.ticker for entry in output.per_ticker]
        if listed != tickers:
            raise ValueError(f"per_ticker must have exactly one entry per ticker in this order: {tickers} "
                             f"(got {listed})")
        if output.preferred is not None and output.preferred not in tickers:
            raise ValueError(f"preferred must be one of {tickers} or null (got {output.preferred!r})")
        unknown = [c for c in output.filing_citations if c not in shown_ids]
        if unknown:
            hint = f"Valid ids: {', '.join(sorted(shown_ids))}." if shown_ids else "No excerpts were shown, so cite none."
            raise ValueError(f"filing_citations contains ids that were not shown: {unknown}. {hint}")

    return check


def compare(states: list[GraphState], client: GroqClient) -> ComparisonOutput:
    """The comparator step: one LLM call over the finished per-ticker research."""
    return call_with_schema(
        client,
        build_comparator_messages(states),
        schema=ComparisonOutput,
        tool_name=COMPARATOR_TOOL_NAME,
        description=COMPARATOR_TOOL_DESCRIPTION,
        model=REASONING_MODEL,
        extra_check=make_comparison_check(states),
    )


def stream_comparison(
    tickers: list[str],
    max_iterations: int = COMPARE_ROUNDS,
    client: GroqClient | None = None,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
) -> Iterator[ComparisonEvent]:
    """Research each ticker (yielding every step, tagged with its ticker), then the comparison."""
    client = client or GroqClient()
    states: list[GraphState] = []
    for ticker in tickers:
        state = GraphState(ticker=ticker, max_iterations=max_iterations)
        for node, update in stream_research(ticker, max_iterations=max_iterations, client=client,
                                            ticker_factory=ticker_factory, filing_retriever=filing_retriever):
            state = state.model_copy(update=update)
            yield ticker, node, update
        states.append(state)
    yield None, "comparator", {"comparison": compare(states, client)}


if __name__ == "__main__":
    if not 3 <= len(sys.argv) <= 4:
        print("Usage: python -m src.core.comparison <TICKER> <TICKER> [<TICKER>]")
        sys.exit(2)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    for symbol, node, update in stream_comparison([arg.upper() for arg in sys.argv[1:]]):
        if node == "comparator":
            result = update["comparison"]
            print("\n=== Comparison ===")
            for entry in result.per_ticker:
                print(f"{entry.ticker} ({entry.stance}): {entry.summary}")
            for difference in result.key_differences:
                print(f"  - {difference}")
            print(f"preferred: {result.preferred} ({result.confidence}) -- {result.preference_reasoning}")
            print(f"citations: {result.filing_citations}")
        else:
            print(f"[{symbol}] {node}")
