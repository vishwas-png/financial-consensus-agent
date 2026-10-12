"""Multi-ticker comparison (Stage 9): ticker-list extraction, the comparison stream, the Comparator."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.comparison import (
    COMPARATOR_TOOL_NAME,
    build_comparator_messages,
    cited_excerpts,
    stream_comparison,
)
from src.core.nodes.analyst import ANALYST_TOOL_NAME
from src.core.nodes.auditor import AUDITOR_TOOL_NAME
from src.core.nodes.critic import CRITIC_TOOL_NAME
from src.core.nodes.risk_officer import RISK_OFFICER_TOOL_NAME
from src.core.schemas import AnalystOutput, CriticFeedback, RiskAssessment, TickerListExtraction
from src.core.state import GraphState
from src.core.structured_output import ToolCallError
from src.core.tool_calling import COMPARISON_TOOL_NAME, NoUSListingError, extract_tickers
from tests.fakes import ToolRoutingClient, low_risk_assessment
from tests.test_guardrail import GROUNDED, analysis
from tests.test_market_data import FakeTicker
from tests.test_tool_calling import ScriptedClient, tool_call_message


def comparison(tickers: list[str], **overrides: Any) -> dict[str, Any]:
    base = {"per_ticker": [{"ticker": t, "summary": f"{t} summary", "stance": "neutral"} for t in tickers],
            "key_differences": ["trailing_pe: AAPL 38.6 vs MSFT 29.8"], "preferred": tickers[1],
            "preference_reasoning": "Lower multiple with higher margins.", "confidence": "medium",
            "filing_citations": []}
    return base | overrides


# ---------- Ticker-list extraction ----------

@pytest.mark.parametrize(("tickers", "expected"), [
    (["AAPL", "MSFT"], ["AAPL", "MSFT"]),
    (["aapl", "msft", "nvda"], ["AAPL", "MSFT", "NVDA"]),
    (["AAPL", "aapl", "MSFT"], ["AAPL", "MSFT"]),   # duplicates removed, order kept
])
def test_ticker_list_schema_valid(tickers: list[str], expected: list[str]) -> None:
    assert TickerListExtraction(tickers=tickers).tickers == expected


@pytest.mark.parametrize("tickers", [["AAPL"], ["AAPL", "AAPL"], ["A", "B", "C", "D"], ["AAPL", "RELIANCE.NS"], []])
def test_ticker_list_schema_invalid(tickers: list[str]) -> None:
    with pytest.raises(ValidationError):
        TickerListExtraction(tickers=tickers)


def test_no_us_listing_clears_tickers() -> None:
    assert TickerListExtraction(tickers=["AAPL", "RIL"], no_us_listing=True).tickers == []


def test_extract_tickers_forces_the_comparison_tool() -> None:
    client = ScriptedClient([tool_call_message('{"tickers": ["aapl", "msft"]}', name=COMPARISON_TOOL_NAME)])
    assert extract_tickers("Apple vs Microsoft", client) == ["AAPL", "MSFT"]
    call = client.calls[0]
    assert call["tool_choice"]["function"]["name"] == COMPARISON_TOOL_NAME
    assert "COMPARE" in call["messages"][0]["content"]


def test_one_ticker_is_repaired_to_two() -> None:
    client = ScriptedClient([tool_call_message('{"tickers": ["AAPL"]}', name=COMPARISON_TOOL_NAME),
                             tool_call_message('{"tickers": ["AAPL", "MSFT"]}', name=COMPARISON_TOOL_NAME)])
    assert extract_tickers("Apple vs Microsoft", client) == ["AAPL", "MSFT"]
    assert "2 to 3 different tickers" in client.calls[1]["messages"][-1]["content"]


def test_four_tickers_twice_fails() -> None:
    four = '{"tickers": ["AAPL", "MSFT", "NVDA", "AMZN"]}'
    client = ScriptedClient([tool_call_message(four, name=COMPARISON_TOOL_NAME)] * 2)
    with pytest.raises(ToolCallError):
        extract_tickers("Apple, Microsoft, Nvidia and Amazon", client)


def test_no_us_listing_raises_without_repair() -> None:
    client = ScriptedClient([tool_call_message('{"no_us_listing": true}', name=COMPARISON_TOOL_NAME)])
    with pytest.raises(NoUSListingError):
        extract_tickers("Apple vs Reliance Industries", client)
    assert len(client.calls) == 1


# ---------- The comparison stream ----------

def comparison_client(**comparator_overrides: Any) -> ToolRoutingClient:
    return ToolRoutingClient({
        ANALYST_TOOL_NAME: analysis,
        CRITIC_TOOL_NAME: lambda n: CriticFeedback(agrees=True, objections=[], missing_context=[]),
        RISK_OFFICER_TOOL_NAME: low_risk_assessment,
        AUDITOR_TOOL_NAME: lambda n: GROUNDED,
        COMPARATOR_TOOL_NAME: lambda n: comparison(["AAPL", "MSFT"], **comparator_overrides),
    })


def run_stream(client: ToolRoutingClient, tickers: list[str]) -> list[tuple[str | None, str, dict[str, Any]]]:
    return list(stream_comparison(tickers, client=client, ticker_factory=lambda s: FakeTicker(),
                                  filing_retriever=lambda t: []))


def test_each_ticker_runs_the_full_graph_then_the_comparator() -> None:
    events = run_stream(comparison_client(), ["AAPL", "MSFT"])
    per_ticker = ["fetch_data", "analyst", "critic", "consensus_check", "risk_officer", "auditor"]
    assert [(t, node) for t, node, _ in events] == (
        [("AAPL", n) for n in per_ticker] + [("MSFT", n) for n in per_ticker] + [(None, "comparator")])
    assert events[-1][2]["comparison"].preferred == "MSFT"


def test_comparator_gets_one_round_per_ticker_by_default() -> None:
    client = comparison_client()
    run_stream(client, ["AAPL", "MSFT"])
    assert len(client.calls_for(ANALYST_TOOL_NAME)) == 2   # one analyst call per ticker
    assert len(client.calls_for(COMPARATOR_TOOL_NAME)) == 1


@pytest.mark.parametrize(("override", "message"), [
    ({"per_ticker": [{"ticker": "MSFT", "summary": "s", "stance": "bullish"},
                     {"ticker": "AAPL", "summary": "s", "stance": "neutral"}]}, "in this order"),
    ({"preferred": "NVDA"}, "preferred must be one of"),
    ({"filing_citations": ["AAPL-10K-2025-1A-999"]}, "not shown"),
])
def test_invalid_comparisons_are_repaired(override: dict[str, Any], message: str) -> None:
    attempts = {"n": 0}

    def comparator(n: int) -> dict[str, Any]:
        attempts["n"] = n
        return comparison(["AAPL", "MSFT"], **override) if n == 1 else comparison(["AAPL", "MSFT"])

    client = comparison_client()
    client.responders[COMPARATOR_TOOL_NAME] = comparator
    events = run_stream(client, ["AAPL", "MSFT"])
    assert attempts["n"] == 2
    assert message in client.calls_for(COMPARATOR_TOOL_NAME)[1]["messages"][-1]["content"]
    assert events[-1][2]["comparison"].preferred == "MSFT"


def test_null_preference_is_allowed() -> None:
    events = run_stream(comparison_client(preferred=None), ["AAPL", "MSFT"])
    assert events[-1][2]["comparison"].preferred is None


# ---------- Comparator prompt stays small ----------

CHUNKS = [
    "[AAPL-10K-2025-1A-003] (Item 1A - Risk Factors) The Company depends on single-source suppliers. " + "x" * 900,
    "[AAPL-10K-2025-7-001] (Item 7 - MD&A) Services net sales grew.",
    "[AAPL-10K-2025-3-002] (Item 3 - Legal Proceedings) Not cited by anyone.",
]


def researched(ticker: str) -> GraphState:
    return GraphState(
        ticker=ticker, raw_data={"valuation": {"trailing_pe": 38.6}, "price_history": [{"date": "d", "close": 1.0}] * 50},
        filing_chunks=CHUNKS if ticker == "AAPL" else [],
        analyst_output=AnalystOutput(thesis=f"{ticker} thesis", supporting_data_points=["trailing_pe = 38.6"],
                                     reasoning="r", confidence="medium", filing_citations=["AAPL-10K-2025-7-001"]
                                     if ticker == "AAPL" else []),
        risk_assessment=RiskAssessment.model_validate(
            {"key_risks": [{"risk": "Suppliers", "severity": "high", "citation": "AAPL-10K-2025-1A-003"}]
             if ticker == "AAPL" else [{"risk": "Valuation", "severity": "low", "citation": None}],
             "what_would_change_the_thesis": ["c"], "overall_risk": "medium"}),
        auditor_verdict=GROUNDED, iteration_count=1,
    )


def test_only_cited_excerpts_are_sent_and_shortened() -> None:
    excerpts = cited_excerpts(researched("AAPL"))
    assert set(excerpts) == {"AAPL-10K-2025-7-001", "AAPL-10K-2025-1A-003"}   # analyst + risk officer citations
    assert len(excerpts["AAPL-10K-2025-1A-003"]) == 400


def test_comparator_prompt_has_briefs_but_no_price_history() -> None:
    system, user = build_comparator_messages([researched("AAPL"), researched("MSFT")])
    content = user["content"]
    assert "Companies to compare, in order: AAPL, MSFT" in content
    assert "price_history" not in content
    assert "AAPL thesis" in content and "MSFT thesis" in content
    assert "Not cited by anyone" not in content
    briefs = json.loads(content.split("Research results (JSON):\n")[1].split("\n\nCited 10-K")[0])
    assert [brief["ticker"] for brief in briefs] == ["AAPL", "MSFT"]
    assert briefs[0]["auditor"] == {"is_grounded": True, "unsupported_claims": []}


# ---------- Live (opt-in) ----------

@pytest.mark.live
def test_live_extract_tickers() -> None:
    from src.clients.groq_client import GroqClient

    assert extract_tickers("Compare Apple with Microsoft", GroqClient()) == ["AAPL", "MSFT"]
