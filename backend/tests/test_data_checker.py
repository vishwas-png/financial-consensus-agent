"""Data Checker (Stage 9): analyst "field = value" data points vs the market data, no LLM."""

import pytest

from src.core.data_checker import check_data_point, check_data_points, known_values
from src.core.schemas import AnalystOutput

RAW = {
    "price": {"current": 336.9, "fifty_two_week_high": 350.12},
    "valuation": {"market_cap": 3_390_000_000_000, "trailing_pe": 39.04, "dividend_yield_pct": 0.44,
                  "beta": None},
    "financials": {"total_revenue": 416_161_000_000, "revenue_growth": 0.079, "profit_margin": 0.2692,
                   "debt_to_equity": 78.4},
    "annual_financials": [{"fiscal_year_end": "2025-09-27", "total_revenue": 416_161_000_000, "net_income": 112_010_000_000},
                          {"fiscal_year_end": "2024-09-28", "total_revenue": 391_035_000_000, "net_income": 93_736_000_000}],
    "profile": {"name": "Apple Inc."},
}
VALUES = known_values(RAW)


@pytest.mark.parametrize("data_point", [
    "trailing_pe = 39.04",
    "trailing_pe = 39.0",                      # rounding
    "trailing_pe = 39x",                       # within 2%
    "market_cap = 3.39T",                      # suffix
    "market_cap = $3.4 trillion",
    "total_revenue = 391,035,000,000",         # an annual_financials row (FY2024)
    "net_income (FY2025) = $112.0B",
    "revenue_growth = 0.079",
    "revenue_growth = 7.9% YoY (quarterly)",   # percentage of a fraction
    "profit_margin = 26.9",                    # percent written without the sign
    "dividend_yield_pct = 0.44%",              # already a percentage field
    "debt_to_equity = 78.4",
    "price.current: 336.90",                   # dotted field, colon
])
def test_matches(data_point: str) -> None:
    assert check_data_point(data_point, VALUES).status == "match", data_point


@pytest.mark.parametrize(("data_point", "actual"), [
    ("trailing_pe = 45.0", 39.04),
    ("market_cap = 2.9T", 3_390_000_000_000),
    ("revenue_growth = 16.4%", 0.079),
    ("net_income = 150B", 112_010_000_000),
    ("revenue_growth = 10%", 0.079),            # a whole-percent claim still has to be close
    ("dividend_yield_pct = 44%", 0.44),         # already a percentage: 44% is a scale error
    ("trailing_pe = 0.39", 39.04),              # no percent conversion for non-fraction fields
])
def test_mismatches_report_the_closest_real_value(data_point: str, actual: float) -> None:
    check = check_data_point(data_point, VALUES)
    assert check.status == "mismatch"
    assert check.actual == actual


@pytest.mark.parametrize(("data_point", "status"), [
    ("Services is the fastest growing segment", "not_checkable"),  # no "field = value"
    ("free_cash_flow = 99B", "unknown_field"),                    # field not in this snapshot
    ("beta = 1.2", "unknown_field"),                              # field present but null
    ("trailing_pe = high", "not_checkable"),                      # no number
])
def test_points_that_cannot_be_checked(data_point: str, status: str) -> None:
    assert check_data_point(data_point, VALUES).status == status


def test_summary_counts() -> None:
    analysis = AnalystOutput(thesis="t", reasoning="r", confidence="medium", supporting_data_points=[
        "trailing_pe = 39.0", "revenue_growth = 16.4%", "Strong brand", "free_cash_flow = 99B"])
    result = check_data_points(analysis, RAW)
    assert (result.matched, result.mismatched, result.unchecked) == (1, 1, 2)
    assert [c.status for c in result.checks] == ["match", "mismatch", "not_checkable", "unknown_field"]
