"""fetch_data node: loads market data and SEC filing excerpts into state. No LLM."""

import logging
from collections.abc import Callable
from typing import Any

import yfinance as yf

from src.core.state import GraphState
from src.rag.edgar import FilingError
from src.tools.market_data import fetch_market_data

logger = logging.getLogger(__name__)

FilingRetriever = Callable[[str], list[str]]


def default_filing_retriever(ticker: str) -> list[str]:
    """Real retrieval: ChromaDB + EDGAR. Imported lazily so tests never load the embedding model."""
    from src.rag.retriever import get_filing_chunks

    return get_filing_chunks(ticker)


def make_fetch_data_node(
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
) -> Callable[[GraphState], dict[str, Any]]:
    """Build the node. Both data sources are injectable so tests don't hit the network."""

    def fetch_data(state: GraphState) -> dict[str, Any]:
        # Market data is required: its errors (InvalidTickerError, TickerNotFoundError,
        # DataSourceError) propagate and stop the run.
        raw_data = fetch_market_data(state.ticker, ticker_factory=ticker_factory)

        # Filings are optional: some tickers (ETFs, foreign issuers) have no 10-K, and EDGAR
        # can be down. The debate still runs on market data; the agents are told no filing exists.
        try:
            filing_chunks = filing_retriever(state.ticker)
        except FilingError as error:
            logger.warning("No SEC filing excerpts for %s: %s", state.ticker, error)
            filing_chunks = []

        return {"raw_data": raw_data, "filing_chunks": filing_chunks}

    return fetch_data
