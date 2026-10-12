"""Stage 2 pipeline: free-text request -> LLM tool call -> validated ticker -> market data.

Usage (manual check):  python -m src.core.tool_calling "How is Apple doing?"
"""

import json
import sys
from collections.abc import Callable
from typing import Any

import yfinance as yf

from src.clients.groq_client import GroqClient, LLMError
from src.config import TOOL_CALL_MODEL
from src.core.prompts import TICKER_EXTRACTION_PROMPT, TICKER_LIST_EXTRACTION_PROMPT
from src.core.schemas import MarketDataToolInput, TickerExtraction, TickerListExtraction
from src.core.structured_output import ToolCallError, call_with_schema
from src.tools.market_data import MarketDataError, fetch_market_data

MARKET_DATA_TOOL_NAME = "get_market_data"
MARKET_DATA_TOOL_DESCRIPTION = "Fetch a market data snapshot (price, valuation, financials) for one US stock ticker."


class NoUSListingError(ToolCallError):
    """The model says the company has no US stock listing (a clean refusal, not an error)."""


def extract_ticker(user_request: str, client: GroqClient) -> MarketDataToolInput:
    """Ask the LLM which ticker the user means. Returns validated tool arguments.

    Raises NoUSListingError if the model reports the company has no US listing.
    """
    messages = [
        {"role": "system", "content": TICKER_EXTRACTION_PROMPT},
        {"role": "user", "content": user_request},
    ]
    extraction = call_with_schema(
        client,
        messages,
        schema=TickerExtraction,
        tool_name=MARKET_DATA_TOOL_NAME,
        description=MARKET_DATA_TOOL_DESCRIPTION,
        model=TOOL_CALL_MODEL,
    )
    if extraction.no_us_listing or extraction.ticker is None:
        raise NoUSListingError("The company in the request has no US stock listing")
    return MarketDataToolInput(ticker=extraction.ticker)


COMPARISON_TOOL_NAME = "get_market_data_for_comparison"
COMPARISON_TOOL_DESCRIPTION = "Fetch market data snapshots for 2 or 3 US stock tickers to compare them."


def extract_tickers(user_request: str, client: GroqClient) -> list[str]:
    """Ask the LLM which 2-3 tickers a comparison request means (Stage 9).

    Raises NoUSListingError if one of the companies has no US listing, and ToolCallError if
    the model can't produce 2-3 distinct valid tickers after the repair attempt.
    """
    messages = [
        {"role": "system", "content": TICKER_LIST_EXTRACTION_PROMPT},
        {"role": "user", "content": user_request},
    ]
    extraction = call_with_schema(
        client,
        messages,
        schema=TickerListExtraction,
        tool_name=COMPARISON_TOOL_NAME,
        description=COMPARISON_TOOL_DESCRIPTION,
        model=TOOL_CALL_MODEL,
    )
    if extraction.no_us_listing:
        raise NoUSListingError("A company in the comparison has no US stock listing")
    return extraction.tickers


def research_request_to_market_data(
    user_request: str,
    client: GroqClient,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
) -> dict[str, Any]:
    """Full Stage 2 path: extract the ticker with the LLM, then execute the tool (yfinance)."""
    tool_input = extract_ticker(user_request, client)
    return fetch_market_data(tool_input.ticker, ticker_factory=ticker_factory)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('Usage: python -m src.core.tool_calling "How is Apple doing?"')
        sys.exit(2)
    try:
        data = research_request_to_market_data(sys.argv[1], GroqClient())
    except (ToolCallError, LLMError, MarketDataError) as error:
        print(f"{type(error).__name__}: {error}")
        sys.exit(1)
    summary = {
        "ticker": data["ticker"],
        "name": data["profile"]["name"],
        "price": data["price"]["current"],
        "market_cap": data["valuation"]["market_cap"],
        "trailing_pe": data["valuation"]["trailing_pe"],
    }
    print(json.dumps(summary, indent=2))
