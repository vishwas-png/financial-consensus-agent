"""fetch_data node: loads market data and SEC filing excerpts into state. No LLM."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import copy_context
from typing import Any

import yfinance as yf

from src.config import FILING_TIMEOUT_SECONDS
from src.core.state import GraphState
from src.rag.edgar import FilingError
from src.rag.embeddings import EmbeddingError
from src.tools.market_data import fetch_market_data

logger = logging.getLogger(__name__)

FilingRetriever = Callable[[str], list[str]]

# One background thread for filing ingestion. A first-time ingestion that overruns the time
# limit keeps going here and is cached in ChromaDB, so the next run for that ticker is fast.
_ingest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="filing-ingest")


def default_filing_retriever(ticker: str) -> list[str]:
    """Real retrieval: ChromaDB + EDGAR. Imported lazily so tests never load the embedding model."""
    from src.rag.retriever import get_filing_chunks

    return get_filing_chunks(ticker)


def make_fetch_data_node(
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
    filing_timeout: float = FILING_TIMEOUT_SECONDS,
) -> Callable[[GraphState], dict[str, Any]]:
    """Build the node. Both data sources are injectable so tests don't hit the network."""

    def fetch_data(state: GraphState) -> dict[str, Any]:
        # Market data is required: its errors (InvalidTickerError, TickerNotFoundError,
        # DataSourceError) propagate and stop the run.
        raw_data = fetch_market_data(state.ticker, ticker_factory=ticker_factory)

        # Filings are optional: some tickers (ETFs, foreign issuers) have no 10-K, and EDGAR
        # can be down. The debate still runs on market data; the agents are told no filing exists.
        # Also bounded in time: a run must never hang on ingestion (it did, live, on a slow CPU).
        # copy_context(): the ingestion thread reports to this run's metrics and log tag
        future = _ingest_pool.submit(copy_context().run, filing_retriever, state.ticker)
        try:
            filing_chunks = future.result(timeout=filing_timeout)
            filing_status = "found" if filing_chunks else "empty"
        except FutureTimeout:
            logger.warning("Filing ingestion for %s exceeded %.0fs; continuing without excerpts",
                           state.ticker, filing_timeout)
            filing_chunks, filing_status = [], "timeout"
        except (FilingError, EmbeddingError) as error:
            logger.warning("No SEC filing excerpts for %s: %s", state.ticker, error)
            filing_chunks, filing_status = [], "unavailable"

        return {"raw_data": raw_data, "filing_chunks": filing_chunks, "filing_status": filing_status}

    return fetch_data
