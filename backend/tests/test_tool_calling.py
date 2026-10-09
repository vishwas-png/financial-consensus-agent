"""Tool calling + structured extraction tests with a scripted fake Groq client (no network)."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.clients.groq_client import MalformedToolCallError
from src.core.schemas import MarketDataToolInput, TickerExtraction
from src.core.structured_output import ToolCallError, call_with_schema, pydantic_to_tool
from src.core.tool_calling import (
    MARKET_DATA_TOOL_NAME,
    NoUSListingError,
    extract_ticker,
    research_request_to_market_data,
)
from tests.test_market_data import FakeTicker


def tool_call_message(arguments: str, name: str = MARKET_DATA_TOOL_NAME) -> SimpleNamespace:
    """An assistant message shaped like the Groq SDK's, containing one tool call."""
    call = SimpleNamespace(id="call_1", type="function", function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(content=None, tool_calls=[call])


def text_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text, tool_calls=None)


class ScriptedClient:
    """Fake GroqClient: returns (or raises) scripted outcomes and records every request."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], model: str, tools: Any = None,
             tool_choice: Any = None, temperature: float = 0.0) -> Any:
        self.calls.append({"messages": messages, "model": model, "tools": tools, "tool_choice": tool_choice})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ---------- Tool definition ----------

def test_pydantic_to_tool_builds_function_schema() -> None:
    tool = pydantic_to_tool(MarketDataToolInput, "get_market_data", "Fetch data")
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "get_market_data"
    params = tool["function"]["parameters"]
    assert params["properties"]["ticker"]["type"] == "string"
    assert params["required"] == ["ticker"]
    assert params["additionalProperties"] is False


# ---------- Happy path ----------

def test_valid_call_returns_validated_ticker() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "AAPL"}')])
    result = extract_ticker("How is Apple doing?", client)
    assert result == MarketDataToolInput(ticker="AAPL")
    assert len(client.calls) == 1


def test_lowercase_ticker_normalized() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "aapl"}')])
    assert extract_ticker("apple", client).ticker == "AAPL"


def test_request_forces_the_tool_and_sends_prompt() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "AAPL"}')])
    extract_ticker("How is Apple doing?", client)
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "function", "function": {"name": MARKET_DATA_TOOL_NAME}}
    assert call["tools"][0]["function"]["name"] == MARKET_DATA_TOOL_NAME
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1] == {"role": "user", "content": "How is Apple doing?"}


# ---------- Repair: bad first answer, good second answer ----------

@pytest.mark.parametrize(
    ("bad_first", "expected_in_repair"),
    [
        (text_message("Apple's ticker is AAPL."), "plain text"),
        (tool_call_message('{"ticker": "AAPL"}', name="get_weather"), "get_weather"),
        (tool_call_message('{"ticker": AAPL'), "not valid JSON"),
        (tool_call_message('{"ticker": "BRK.B"}'), "exchange suffix"),
        (tool_call_message('{"ticker": "AAPL", "period": "1y"}'), "period"),
        (tool_call_message('{}'), "ticker"),
        (MalformedToolCallError("Tool choice is required", failed_generation="No US ticker"), "No US ticker"),
    ],
    ids=["plain-text", "wrong-tool", "bad-json", "dot-suffix", "extra-field", "missing-field", "groq-tool-use-failed"],
)
def test_bad_first_answer_is_repaired(bad_first: Any, expected_in_repair: str) -> None:
    client = ScriptedClient([bad_first, tool_call_message('{"ticker": "AAPL"}')])
    assert extract_ticker("Apple", client).ticker == "AAPL"
    assert len(client.calls) == 2

    # The repair request = original messages + one user message explaining the problem
    repair_messages = client.calls[1]["messages"]
    assert repair_messages[:2] == client.calls[0]["messages"]
    assert repair_messages[-1]["role"] == "user"
    assert expected_in_repair in repair_messages[-1]["content"]


def test_caller_messages_not_mutated() -> None:
    client = ScriptedClient([text_message("nope"), tool_call_message('{"ticker": "AAPL"}')])
    messages = [{"role": "user", "content": "Apple"}]
    call_with_schema(client, messages, MarketDataToolInput, MARKET_DATA_TOOL_NAME, "d", "m")
    assert messages == [{"role": "user", "content": "Apple"}]


# ---------- extra_check hook ----------

def reject_msft(result: MarketDataToolInput) -> None:
    if result.ticker == "MSFT":
        raise ValueError("MSFT is not the company the user asked about")


def test_extra_check_failure_triggers_repair_with_message() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "MSFT"}'), tool_call_message('{"ticker": "AAPL"}')])
    result = call_with_schema(client, [{"role": "user", "content": "Apple"}], MarketDataToolInput,
                              MARKET_DATA_TOOL_NAME, "d", "m", extra_check=reject_msft)
    assert result.ticker == "AAPL"
    assert len(client.calls) == 2
    repair = client.calls[1]["messages"][-1]["content"]
    assert "content check" in repair and "MSFT is not the company" in repair


def test_extra_check_failing_twice_raises() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "MSFT"}')] * 2)
    with pytest.raises(ToolCallError) as excinfo:
        call_with_schema(client, [{"role": "user", "content": "Apple"}], MarketDataToolInput,
                         MARKET_DATA_TOOL_NAME, "d", "m", extra_check=reject_msft)
    assert "MSFT is not the company" in excinfo.value.reason
    assert excinfo.value.raw_output == '{"ticker":"MSFT"}'


def test_extra_check_passing_needs_one_call() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "AAPL"}')])
    call_with_schema(client, [{"role": "user", "content": "Apple"}], MarketDataToolInput,
                     MARKET_DATA_TOOL_NAME, "d", "m", extra_check=reject_msft)
    assert len(client.calls) == 1


# ---------- Repair fails ----------

def test_two_bad_answers_raise_tool_call_error() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "RELIANCE.NS"}'), tool_call_message('{"ticker": "RELIANCE.NS"}')])
    with pytest.raises(ToolCallError) as excinfo:
        extract_ticker("Reliance Industries", client)
    assert len(client.calls) == 2
    assert "exchange suffix" in excinfo.value.reason
    assert excinfo.value.raw_output == '{"ticker": "RELIANCE.NS"}'


def test_two_refusals_raise_tool_call_error() -> None:
    refusal = MalformedToolCallError("Tool choice is required", failed_generation="Not a US company")
    client = ScriptedClient([refusal, refusal])
    with pytest.raises(ToolCallError) as excinfo:
        extract_ticker("Reliance Industries", client)
    assert excinfo.value.raw_output == "Not a US company"


# ---------- End to end (LLM -> tool) ----------

def test_research_request_to_market_data_runs_the_tool() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "aapl"}')])
    requested: list[str] = []

    def factory(symbol: str) -> FakeTicker:
        requested.append(symbol)
        return FakeTicker()

    data = research_request_to_market_data("How is Apple doing?", client, ticker_factory=factory)
    assert requested == ["AAPL"]
    assert data["ticker"] == "AAPL"
    json.dumps(data)


# ---------- Live (opt-in: pytest -m live) ----------

@pytest.mark.live
@pytest.mark.parametrize(
    ("request_text", "expected"),
    [("How is Apple doing?", "AAPL"), ("Thoughts on Tesla stock", "TSLA"),
     ("Berkshire Hathaway class B shares", "BRK-B")],
)
def test_live_extract_ticker(request_text: str, expected: str) -> None:
    from src.clients.groq_client import GroqClient

    assert extract_ticker(request_text, GroqClient()).ticker == expected


@pytest.mark.live
def test_live_end_to_end_microsoft() -> None:
    from src.clients.groq_client import GroqClient

    data = research_request_to_market_data("Give me a read on Microsoft", GroqClient())
    assert data["ticker"] == "MSFT"
    assert data["price"]["current"] is not None


# ---------- "No US listing" escape hatch (Stage 8) ----------



@pytest.mark.parametrize(("payload", "expected_ticker"), [
    ({"ticker": "aapl"}, "AAPL"),
    ({"ticker": "BRK-B", "no_us_listing": False}, "BRK-B"),
    ({"no_us_listing": True}, None),
    ({"ticker": "RIL", "no_us_listing": True}, None),  # a ticker next to no_us_listing is ignored
])
def test_ticker_extraction_schema_valid(payload: dict, expected_ticker: str | None) -> None:
    assert TickerExtraction.model_validate(payload).ticker == expected_ticker


@pytest.mark.parametrize("payload", [{}, {"no_us_listing": False}, {"ticker": "RELIANCE.NS"}, {"ticker": "AAPL", "x": 1}])
def test_ticker_extraction_schema_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TickerExtraction.model_validate(payload)


def test_no_us_listing_raises_without_a_repair_attempt() -> None:
    client = ScriptedClient([tool_call_message('{"no_us_listing": true}')])
    with pytest.raises(NoUSListingError):
        extract_ticker("What do you think of Reliance Industries?", client)
    assert len(client.calls) == 1  # a clean refusal is not a mistake to repair


def test_prompt_tells_the_model_how_to_decline() -> None:
    client = ScriptedClient([tool_call_message('{"ticker": "AAPL"}')])
    extract_ticker("Apple", client)
    assert "no_us_listing" in client.calls[0]["messages"][0]["content"]
    assert "no_us_listing" in json.dumps(client.calls[0]["tools"][0]["function"]["parameters"])
