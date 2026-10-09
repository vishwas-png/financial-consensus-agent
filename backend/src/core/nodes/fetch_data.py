"""fetch_data node: loads market data for the ticker into state. No LLM."""

from collections.abc import Callable
from typing import Any

import yfinance as yf

from src.core.state import GraphState
from src.tools.market_data import fetch_market_data


def make_fetch_data_node(ticker_factory: Callable[[str], Any] = yf.Ticker) -> Callable[[GraphState], dict[str, Any]]:
    """Build the node. `ticker_factory` is injectable so tests don't hit the network."""

    def fetch_data(state: GraphState) -> dict[str, Any]:
        # Errors (InvalidTickerError, TickerNotFoundError, DataSourceError) propagate and stop the run.
        # filing_chunks is filled by RAG retrieval in Stage 4.
        return {"raw_data": fetch_market_data(state.ticker, ticker_factory=ticker_factory)}

    return fetch_data
