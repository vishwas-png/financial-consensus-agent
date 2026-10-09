"""Tests for the yfinance wrapper. All offline (FakeTicker) except one opt-in live test."""

import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.tools.market_data import (
    DataSourceError,
    InvalidTickerError,
    TickerNotFoundError,
    fetch_market_data,
)


def make_income_stmt(years: list[int]) -> pd.DataFrame:
    """Income statement shaped like yfinance: rows = line items, columns = fiscal year ends."""
    columns = [pd.Timestamp(f"{year}-09-30") for year in years]
    return pd.DataFrame(
        {
            col: {"Total Revenue": 1000.0 + i, "Net Income": 100.0 + i}
            for i, col in enumerate(columns)
        }
    )


def make_history(closes: list[float]) -> pd.DataFrame:
    """Weekly price history shaped like yfinance (tz-aware index)."""
    index = pd.date_range("2025-01-06", periods=len(closes), freq="W-MON", tz="America/New_York")
    return pd.DataFrame({"Close": closes}, index=index)


FULL_INFO: dict[str, Any] = {
    "longName": "Apple Inc.",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "country": "United States",
    "currency": "USD",
    "exchange": "NMS",
    "currentPrice": 340.42,
    "previousClose": 336.67,
    "fiftyTwoWeekHigh": 345.34,
    "fiftyTwoWeekLow": 243.42,
    "marketCap": 4968150990848,
    "trailingPE": 39.04,
    "forwardPE": 35.51,
    "priceToBook": 46.25,
    "dividendYield": 0.32,
    "beta": 1.069,
    "totalRevenue": 466822987776,
    "revenueGrowth": 0.164,
    "netIncomeToCommon": 128929996800,
    "profitMargins": 0.276,
    "freeCashflow": 107721875456,
    "totalDebt": 84343996416,
    "totalCash": 62399000576,
    "debtToEquity": 78.445,
}


class FakeTicker:
    """Stands in for yfinance.Ticker with the three attributes we use."""

    def __init__(
        self,
        info: dict[str, Any] | None = None,
        history: pd.DataFrame | None = None,
        income_stmt: pd.DataFrame | None = None,
    ) -> None:
        self.info = FULL_INFO if info is None else info
        self._history = make_history([330.0, 335.5, 340.42]) if history is None else history
        self.income_stmt = make_income_stmt([2025, 2024, 2023]) if income_stmt is None else income_stmt

    def history(self, period: str, interval: str) -> pd.DataFrame:
        return self._history


class RecordingFactory:
    """Ticker factory that records which symbols it was called with."""

    def __init__(self, fake: FakeTicker) -> None:
        self.fake = fake
        self.calls: list[str] = []

    def __call__(self, symbol: str) -> FakeTicker:
        self.calls.append(symbol)
        return self.fake


# ---------- Happy path ----------

def test_returns_all_sections_and_is_json_serializable() -> None:
    data = fetch_market_data("AAPL", ticker_factory=lambda s: FakeTicker())
    assert set(data) == {
        "ticker", "fetched_at", "profile", "price", "valuation",
        "financials", "annual_financials", "price_history",
    }
    assert data["profile"]["name"] == "Apple Inc."
    assert data["price"]["current"] == 340.42
    assert data["valuation"]["dividend_yield_pct"] == 0.32
    assert data["financials"]["revenue_growth"] == 0.164
    json.dumps(data)  # must not raise


def test_lowercase_ticker_is_normalized_before_fetch() -> None:
    factory = RecordingFactory(FakeTicker())
    data = fetch_market_data("aapl", ticker_factory=factory)
    assert factory.calls == ["AAPL"]
    assert data["ticker"] == "AAPL"


# ---------- Missing / dirty values ----------

def test_missing_info_keys_become_none() -> None:
    data = fetch_market_data("XYZ", ticker_factory=lambda s: FakeTicker(info={"currentPrice": 10.0}))
    assert data["price"]["current"] == 10.0
    assert data["profile"]["name"] is None
    assert data["valuation"]["trailing_pe"] is None
    assert data["financials"]["free_cash_flow"] is None


def test_falls_back_to_regular_market_price() -> None:
    data = fetch_market_data("XYZ", ticker_factory=lambda s: FakeTicker(info={"regularMarketPrice": 12.5}))
    assert data["price"]["current"] == 12.5


def test_nan_inf_and_numpy_values_are_cleaned() -> None:
    info = FULL_INFO | {
        "trailingPE": float("nan"),
        "forwardPE": np.float64(20.5),
        "marketCap": np.int64(1000),
        "beta": float("inf"),
    }
    data = fetch_market_data("AAPL", ticker_factory=lambda s: FakeTicker(info=info))
    assert data["valuation"]["trailing_pe"] is None
    assert data["valuation"]["forward_pe"] == 20.5 and type(data["valuation"]["forward_pe"]) is float
    assert data["valuation"]["market_cap"] == 1000 and type(data["valuation"]["market_cap"]) is int
    assert data["valuation"]["beta"] is None
    json.dumps(data)


# ---------- Annual financials ----------

def test_annual_financials_capped_at_four_newest_first() -> None:
    stmt = make_income_stmt([2021, 2025, 2022, 2024, 2023])
    data = fetch_market_data("AAPL", ticker_factory=lambda s: FakeTicker(income_stmt=stmt))
    years = [row["fiscal_year_end"] for row in data["annual_financials"]]
    assert years == ["2025-09-30", "2024-09-30", "2023-09-30", "2022-09-30"]
    assert isinstance(data["annual_financials"][0]["total_revenue"], float)


def test_empty_income_statement_gives_empty_list() -> None:
    data = fetch_market_data("AAPL", ticker_factory=lambda s: FakeTicker(income_stmt=pd.DataFrame()))
    assert data["annual_financials"] == []


def test_missing_income_row_becomes_none() -> None:
    stmt = make_income_stmt([2025]).drop(index="Net Income")
    data = fetch_market_data("AAPL", ticker_factory=lambda s: FakeTicker(income_stmt=stmt))
    assert data["annual_financials"][0]["net_income"] is None


# ---------- Price history ----------

def test_price_history_dates_are_iso_and_nan_weeks_skipped() -> None:
    history = make_history([100.0, float("nan"), 102.0])
    data = fetch_market_data("AAPL", ticker_factory=lambda s: FakeTicker(history=history))
    assert data["price_history"] == [
        {"date": "2025-01-06", "close": 100.0},
        {"date": "2025-01-20", "close": 102.0},
    ]


# ---------- Errors ----------

def test_unknown_ticker_raises_not_found() -> None:
    # This is what yfinance actually returns for a symbol that doesn't exist
    fake = FakeTicker(info={"trailingPegRatio": None}, history=pd.DataFrame())
    with pytest.raises(TickerNotFoundError):
        fetch_market_data("ZZZZZ", ticker_factory=lambda s: fake)


@pytest.mark.parametrize("bad", ["RELIANCE.NS", "", "   ", "TOOLONG"])
def test_invalid_ticker_rejected_before_any_fetch(bad: str) -> None:
    factory = RecordingFactory(FakeTicker())
    with pytest.raises(InvalidTickerError):
        fetch_market_data(bad, ticker_factory=factory)
    assert factory.calls == []


def test_network_failure_wrapped_as_data_source_error() -> None:
    def failing_factory(symbol: str) -> FakeTicker:
        raise ConnectionError("network down")

    with pytest.raises(DataSourceError) as excinfo:
        fetch_market_data("AAPL", ticker_factory=failing_factory)
    assert isinstance(excinfo.value.__cause__, ConnectionError)


# ---------- Live (opt-in: pytest -m live) ----------

@pytest.mark.live
def test_live_fetch_aapl() -> None:
    data = fetch_market_data("AAPL")
    assert data["ticker"] == "AAPL"
    assert data["price"]["current"] is not None
    assert len(data["price_history"]) > 40
    assert data["annual_financials"]
    json.dumps(data)
