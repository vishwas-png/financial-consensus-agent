"""Node tests: each node builds the right prompt and returns only its own state fields."""

import pytest

from src.core.nodes.analyst import ANALYST_TOOL_NAME, build_analyst_messages, make_analyst_node
from src.core.nodes.critic import CRITIC_TOOL_NAME, build_critic_messages, make_critic_node
from src.core.nodes.fetch_data import make_fetch_data_node
from src.config import REASONING_MODEL
from src.core.schemas import AnalystOutput, CriticFeedback
from src.core.state import GraphState
from src.core.structured_output import ToolCallError
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


# ---------- fetch_data ----------

def test_fetch_data_fills_raw_data_only() -> None:
    node = make_fetch_data_node(ticker_factory=lambda s: FakeTicker())
    update = node(GraphState(ticker="AAPL"))
    assert set(update) == {"raw_data"}
    assert update["raw_data"]["ticker"] == "AAPL"


def test_fetch_data_propagates_not_found() -> None:
    import pandas as pd

    empty = FakeTicker(info={"trailingPegRatio": None}, history=pd.DataFrame())
    node = make_fetch_data_node(ticker_factory=lambda s: empty)
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
