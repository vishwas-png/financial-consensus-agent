"""yfinance wrapper: ticker in, plain JSON-safe dict out.

No LLM and no financial reasoning here. This module only fetches a curated set
of fields and converts pandas/numpy values into plain Python types, so the result
can be stored in GraphState, saved as JSON in the DB, and streamed over SSE.

Usage (manual check):  python -m src.tools.market_data AAPL
"""

import json
import math
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from pydantic import ValidationError

from src.core.schemas import MarketDataToolInput

# How many fiscal years of income statement data to keep
MAX_ANNUAL_YEARS = 4

# Our field name -> yfinance `Ticker.info` key, grouped by output section.
# Missing keys become None. Units are kept as yfinance returns them:
#   - fractions (0.16 = 16%): revenue_growth, profit_margin
#   - percent (0.32 = 0.32%): dividend_yield_pct  (yfinance reports this one in percent)
#   - debt_to_equity is a percent too (78.4 = 0.784x)
INFO_FIELDS: dict[str, dict[str, str]] = {
    "profile": {
        "name": "longName",
        "sector": "sector",
        "industry": "industry",
        "country": "country",
        "currency": "currency",
        "exchange": "exchange",
    },
    "price": {
        "current": "currentPrice",
        "previous_close": "previousClose",
        "fifty_two_week_high": "fiftyTwoWeekHigh",
        "fifty_two_week_low": "fiftyTwoWeekLow",
    },
    "valuation": {
        "market_cap": "marketCap",
        "trailing_pe": "trailingPE",
        "forward_pe": "forwardPE",
        "price_to_book": "priceToBook",
        "dividend_yield_pct": "dividendYield",
        "beta": "beta",
    },
    "financials": {
        "total_revenue": "totalRevenue",
        "revenue_growth": "revenueGrowth",
        "net_income": "netIncomeToCommon",
        "profit_margin": "profitMargins",
        "free_cash_flow": "freeCashflow",
        "total_debt": "totalDebt",
        "total_cash": "totalCash",
        "debt_to_equity": "debtToEquity",
    },
}


# ---------- Exceptions ----------

class MarketDataError(Exception):
    """Base class for all market data errors."""


class InvalidTickerError(MarketDataError):
    """The ticker string is malformed (empty, exchange suffix, wrong format)."""


class TickerNotFoundError(MarketDataError):
    """The ticker is well-formed but yfinance has no data for it."""


class DataSourceError(MarketDataError):
    """yfinance or the network failed (timeout, rate limit, unexpected response)."""


# ---------- Conversion helpers ----------

def _clean(value: Any) -> Any:
    """Convert numpy/pandas scalars to plain Python; NaN, inf and NA become None."""
    if value is None:
        return None
    # numpy scalars (np.float64, np.int64, ...) expose .item() to get the Python value
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass  # pd.isna fails on lists/dicts; those are passed through unchanged
    if isinstance(value, float) and math.isinf(value):
        return None
    return value


def _pick(info: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Build one output section from `info` using a {our_name: yfinance_key} mapping."""
    return {our_name: _clean(info.get(yf_key)) for our_name, yf_key in mapping.items()}


def _annual_financials(income_stmt: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Up to MAX_ANNUAL_YEARS rows of {fiscal_year_end, total_revenue, net_income}, newest first."""
    if income_stmt is None or income_stmt.empty:
        return []

    def row_value(row_name: str, column: Any) -> Any:
        if row_name not in income_stmt.index:
            return None
        return _clean(income_stmt.at[row_name, column])

    # Columns are fiscal year-end dates; sort newest first to be safe
    columns = sorted(income_stmt.columns, reverse=True)[:MAX_ANNUAL_YEARS]
    return [
        {
            "fiscal_year_end": pd.Timestamp(column).date().isoformat(),
            "total_revenue": row_value("Total Revenue", column),
            "net_income": row_value("Net Income", column),
        }
        for column in columns
    ]


def _price_history(history: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Convert a yfinance history frame into [{date, close}], oldest first."""
    if history is None or history.empty or "Close" not in history.columns:
        return []
    points = []
    for timestamp, close in history["Close"].items():
        close = _clean(close)
        if close is None:
            continue  # skip weeks with no trading data
        points.append({"date": pd.Timestamp(timestamp).date().isoformat(), "close": float(close)})
    return points


# ---------- Public API ----------

def fetch_market_data(
    ticker: str,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
) -> dict[str, Any]:
    """Fetch a curated market data snapshot for one ticker.

    Args:
        ticker: user or LLM supplied ticker, e.g. "aapl" or "BRK-B".
        ticker_factory: builds the yfinance Ticker object. Tests pass a fake here.

    Raises:
        InvalidTickerError: ticker fails validation (checked before any network call).
        TickerNotFoundError: yfinance has no price and no history for the ticker.
        DataSourceError: yfinance or the network raised an exception.
    """
    try:
        symbol = MarketDataToolInput(ticker=ticker).ticker
    except ValidationError as exc:
        raise InvalidTickerError(f"Invalid ticker {ticker!r}: {exc.errors()[0]['msg']}") from exc

    try:
        yf_ticker = ticker_factory(symbol)
        info: dict[str, Any] = yf_ticker.info or {}
        history = yf_ticker.history(period="1y", interval="1wk")
        income_stmt = yf_ticker.income_stmt
    except Exception as exc:  # yfinance raises many different exception types
        raise DataSourceError(f"Failed to fetch data for {symbol}: {exc}") from exc

    # yfinance does not raise for unknown symbols: it returns a near-empty info
    # dict and an empty history frame. Treat "no price and no history" as not found.
    price_section = _pick(info, INFO_FIELDS["price"])
    if price_section["current"] is None:
        # Some tickers only fill regularMarketPrice
        price_section["current"] = _clean(info.get("regularMarketPrice"))
    price_history = _price_history(history)
    if price_section["current"] is None and not price_history:
        raise TickerNotFoundError(f"No market data found for ticker {symbol}")

    return {
        "ticker": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "profile": _pick(info, INFO_FIELDS["profile"]),
        "price": price_section,
        "valuation": _pick(info, INFO_FIELDS["valuation"]),
        "financials": _pick(info, INFO_FIELDS["financials"]),
        "annual_financials": _annual_financials(income_stmt),
        "price_history": price_history,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m src.tools.market_data <TICKER>")
        sys.exit(2)
    try:
        print(json.dumps(fetch_market_data(sys.argv[1]), indent=2))
    except MarketDataError as error:
        print(f"{type(error).__name__}: {error}")
        sys.exit(1)
