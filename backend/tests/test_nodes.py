"""Node tests: each node builds the right prompt and returns only its own state fields."""

import pytest

from src.core.nodes.analyst import ANALYST_TOOL_NAME, build_analyst_messages, make_analyst_node
from src.core.nodes.critic import CRITIC_TOOL_NAME, build_critic_messages, make_critic_node
from src.core.nodes.fetch_data import make_fetch_data_node
from src.config import REASONING_MODEL
from src.core.schemas import AnalystOutput, CriticFeedback
from src.core.state import GraphState
from src.core.nodes.context import chunk_ids, evidence_block
from src.core.structured_output import ToolCallError
from src.rag.edgar import FilingNotFoundError
from src.tools.market_data import TickerNotFoundError
from tests.fakes import ToolRoutingClient, tool_call_message
from tests.test_market_data import FakeTicker

ANALYSIS = AnalystOutput(
    thesis="AAPL is fairly valued.",
    supporting_data_points=["trailing_pe = 39.0"],
    reasoning="High multiple offset by strong margins.",
    confidence="medium",
)
OBJECTING = CriticFeedback(agrees=False, objections=["Ignores debt_to_equity = 78.4"], missing_context=["FCF trend"])
RAW_DATA = {"ticker": "AAPL", "valuation": {"trailing_pe": 39.0}}
CHUNKS = [
    "[AAPL-10K-2025-1A-003] (Item 1A - Risk Factors) The Company depends on single-source suppliers.",
    "[AAPL-10K-2025-7-001] (Item 7 - Management's Discussion and Analysis) Services net sales grew.",
]


# ---------- fetch_data ----------

def test_fetch_data_fills_market_data_and_filing_chunks() -> None:
    requested: list[str] = []

    def retriever(ticker: str) -> list[str]:
        requested.append(ticker)
        return CHUNKS

    node = make_fetch_data_node(ticker_factory=lambda s: FakeTicker(), filing_retriever=retriever)
    update = node(GraphState(ticker="AAPL"))
    assert set(update) == {"raw_data", "filing_chunks"}
    assert update["raw_data"]["ticker"] == "AAPL"
    assert update["filing_chunks"] == CHUNKS
    assert requested == ["AAPL"]


def test_fetch_data_continues_without_filings_on_filing_error(caplog: pytest.LogCaptureFixture) -> None:
    def missing(ticker: str) -> list[str]:
        raise FilingNotFoundError("No 10-K found on EDGAR for SPY")

    node = make_fetch_data_node(ticker_factory=lambda s: FakeTicker(), filing_retriever=missing)
    update = node(GraphState(ticker="SPY"))
    assert update["filing_chunks"] == []
    assert update["raw_data"]["ticker"] == "SPY"
    assert "No SEC filing excerpts for SPY" in caplog.text


def test_fetch_data_does_not_hide_unexpected_retriever_bugs() -> None:
    def broken(ticker: str) -> list[str]:
        raise KeyError("bug")

    node = make_fetch_data_node(ticker_factory=lambda s: FakeTicker(), filing_retriever=broken)
    with pytest.raises(KeyError):
        node(GraphState(ticker="AAPL"))


def test_fetch_data_propagates_not_found() -> None:
    import pandas as pd

    empty = FakeTicker(info={"trailingPegRatio": None}, history=pd.DataFrame())
    node = make_fetch_data_node(ticker_factory=lambda s: empty, filing_retriever=lambda t: [])
    with pytest.raises(TickerNotFoundError):
        node(GraphState(ticker="ZZZZQ"))


# ---------- analyst ----------

def test_analyst_first_round_prompt_has_data_but_no_feedback() -> None:
    messages = build_analyst_messages(GraphState(ticker="AAPL", raw_data=RAW_DATA))
    assert messages[0]["role"] == "system"
    assert '"trailing_pe": 39.0' in messages[1]["content"]
    assert "REVISION" not in messages[1]["content"]


def test_analyst_revision_prompt_includes_previous_output_and_objections() -> None:
    state = GraphState(ticker="AAPL", raw_data=RAW_DATA, analyst_output=ANALYSIS, critic_feedback=OBJECTING)
    content = build_analyst_messages(state)[1]["content"]
    assert "REVISION" in content
    assert "AAPL is fairly valued." in content
    assert "Ignores debt_to_equity = 78.4" in content
    assert "FCF trend" in content


def test_analyst_node_returns_only_analyst_output() -> None:
    client = ToolRoutingClient({ANALYST_TOOL_NAME: lambda n: ANALYSIS})
    update = make_analyst_node(client)(GraphState(ticker="AAPL", raw_data=RAW_DATA))
    assert update == {"analyst_output": ANALYSIS}
    assert client.calls[0]["model"] == REASONING_MODEL


# ---------- critic ----------

def test_critic_prompt_includes_data_and_thesis() -> None:
    state = GraphState(ticker="AAPL", raw_data=RAW_DATA, analyst_output=ANALYSIS)
    content = build_critic_messages(state)[1]["content"]
    assert '"trailing_pe": 39.0' in content
    assert "AAPL is fairly valued." in content
    assert "round 1" in content


def test_critic_requires_analyst_output() -> None:
    with pytest.raises(ValueError):
        build_critic_messages(GraphState(ticker="AAPL", raw_data=RAW_DATA))


def test_critic_node_returns_only_critic_feedback() -> None:
    client = ToolRoutingClient({CRITIC_TOOL_NAME: lambda n: OBJECTING})
    update = make_critic_node(client)(GraphState(ticker="AAPL", raw_data=RAW_DATA, analyst_output=ANALYSIS))
    assert update == {"critic_feedback": OBJECTING}


# ---------- error propagation ----------

class AlwaysInvalidClient:
    """Returns schema-invalid analyst output every time (missing required fields)."""

    def chat(self, messages, model, tools=None, tool_choice=None, temperature=0.0):  # type: ignore[no-untyped-def]
        return tool_call_message('{"thesis": "incomplete"}', ANALYST_TOOL_NAME)


def test_analyst_node_propagates_tool_call_error() -> None:
    with pytest.raises(ToolCallError):
        make_analyst_node(AlwaysInvalidClient())(GraphState(ticker="AAPL", raw_data=RAW_DATA))


# ---------- Filing evidence + citations (Stage 4) ----------

def test_evidence_block_lists_excerpts_with_ids() -> None:
    state = GraphState(ticker="AAPL", raw_data=RAW_DATA, filing_chunks=CHUNKS)
    block = evidence_block(state)
    assert "SEC filing excerpts (latest 10-K)" in block
    assert "[AAPL-10K-2025-1A-003]" in block and "single-source suppliers" in block
    assert chunk_ids(state) == {"AAPL-10K-2025-1A-003", "AAPL-10K-2025-7-001"}


def test_evidence_block_says_when_no_filing() -> None:
    block = evidence_block(GraphState(ticker="SPY", raw_data=RAW_DATA))
    assert "none available" in block


def test_critic_sees_same_excerpts_as_analyst() -> None:
    state = GraphState(ticker="AAPL", raw_data=RAW_DATA, filing_chunks=CHUNKS, analyst_output=ANALYSIS)
    analyst_content = build_analyst_messages(state)[1]["content"]
    critic_content = build_critic_messages(state)[1]["content"]
    for chunk in CHUNKS:
        assert chunk in analyst_content and chunk in critic_content


def cited(citations: list[str]) -> AnalystOutput:
    return ANALYSIS.model_copy(update={"filing_citations": citations})


def test_valid_citations_accepted_in_one_call() -> None:
    client = ToolRoutingClient({ANALYST_TOOL_NAME: lambda n: cited(["AAPL-10K-2025-1A-003"])})
    state = GraphState(ticker="AAPL", raw_data=RAW_DATA, filing_chunks=CHUNKS)
    update = make_analyst_node(client)(state)
    assert update["analyst_output"].filing_citations == ["AAPL-10K-2025-1A-003"]
    assert len(client.calls) == 1


def test_invented_citation_triggers_repair() -> None:
    # Round 1: cites an id we never provided. Repair: cites a real one.
    client = ToolRoutingClient({ANALYST_TOOL_NAME: lambda n: cited(["AAPL-10K-2025-1A-999"] if n == 1 else ["AAPL-10K-2025-7-001"])})
    state = GraphState(ticker="AAPL", raw_data=RAW_DATA, filing_chunks=CHUNKS)
    update = make_analyst_node(client)(state)
    assert update["analyst_output"].filing_citations == ["AAPL-10K-2025-7-001"]
    assert len(client.calls) == 2
    repair_message = client.calls[1]["messages"][-1]["content"]
    assert "AAPL-10K-2025-1A-999" in repair_message and "Valid ids" in repair_message


def test_citations_without_any_excerpts_rejected() -> None:
    client = ToolRoutingClient({ANALYST_TOOL_NAME: lambda n: cited(["AAPL-10K-2025-1A-003"])})
    with pytest.raises(ToolCallError) as excinfo:
        make_analyst_node(client)(GraphState(ticker="AAPL", raw_data=RAW_DATA))
    assert "No excerpts were provided" in excinfo.value.reason


def test_bracketed_citation_rejected_with_hint() -> None:
    # Models sometimes copy the brackets; the id must be exact
    client = ToolRoutingClient({ANALYST_TOOL_NAME: lambda n: cited(["[AAPL-10K-2025-1A-003]"] if n == 1 else ["AAPL-10K-2025-1A-003"])})
    update = make_analyst_node(client)(GraphState(ticker="AAPL", raw_data=RAW_DATA, filing_chunks=CHUNKS))
    assert update["analyst_output"].filing_citations == ["AAPL-10K-2025-1A-003"]
    assert len(client.calls) == 2
