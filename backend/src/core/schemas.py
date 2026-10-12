"""Pydantic v2 schemas: the structured contracts between the LLM agents and our code.

Every LLM response is parsed into one of these models. If the LLM returns a
missing field, a wrong type, or an extra field it invented, validation fails
loudly instead of letting bad data flow further down the graph.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A US ticker: 1-5 letters, optionally a class-share suffix like "-B" (BRK-B, yfinance format).
TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


class AnalystOutput(BaseModel):
    """The Analyst agent's investment thesis for one ticker."""

    model_config = ConfigDict(extra="forbid")

    thesis: str
    supporting_data_points: list[str]
    reasoning: str
    confidence: Literal["low", "medium", "high"]
    filing_citations: list[str] = Field(default_factory=list)


class CriticFeedback(BaseModel):
    """The Critic agent's review of the Analyst's thesis."""

    model_config = ConfigDict(extra="forbid")

    agrees: bool
    objections: list[str]
    missing_context: list[str]


class AuditorVerdict(BaseModel):
    """The Auditor (LLM-as-a-Judge) verdict: are the final claims backed by filing text?"""

    model_config = ConfigDict(extra="forbid")

    is_grounded: bool
    unsupported_claims: list[str]
    notes: str


class RiskItem(BaseModel):
    """One material risk to the thesis, as judged by the Risk Officer."""

    model_config = ConfigDict(extra="forbid")

    risk: str
    severity: Literal["low", "medium", "high"]
    # Excerpt id the risk comes from (no brackets), or null when it comes from the market data
    citation: str | None = None


class RiskAssessment(BaseModel):
    """The Risk Officer's review of the final thesis (Stage 9)."""

    model_config = ConfigDict(extra="forbid")

    key_risks: list[RiskItem] = Field(min_length=1, max_length=5)
    what_would_change_the_thesis: list[str] = Field(min_length=1, max_length=4)
    overall_risk: Literal["low", "medium", "high"]


class DataPointCheck(BaseModel):
    """Code check of one analyst data point ("field = value") against the market data."""

    data_point: str
    field: str | None = None
    claimed: float | None = None
    actual: float | None = None   # the closest value found for that field
    status: Literal["match", "mismatch", "unknown_field", "not_checkable"]


class DataCheck(BaseModel):
    """Result of the Data Checker (no LLM) for the final analysis."""

    checks: list[DataPointCheck]
    matched: int
    mismatched: int
    unchecked: int


class MarketDataToolInput(BaseModel):
    """Arguments for the market-data tool call. The LLM fills this in; we validate it."""

    model_config = ConfigDict(extra="forbid")

    ticker: str

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, value: str) -> str:
        """Normalize to uppercase, then reject empty tickers and exchange suffixes."""
        ticker = value.strip().upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        if "." in ticker:
            # e.g. "RELIANCE.NS" or "VOD.L": only US listings are supported.
            raise ValueError("ticker must not include an exchange suffix (e.g. '.NS')")
        if not TICKER_PATTERN.fullmatch(ticker):
            raise ValueError("ticker must be 1-5 letters, optionally followed by a class like '-B'")
        return ticker


class TickerExtraction(BaseModel):
    """What the extraction LLM call returns: a US ticker, OR an explicit "no US listing".

    Without the escape hatch, a forced tool call pushes the model to invent a ticker for
    companies that only trade abroad (seen live: Reliance Industries -> "RIL").
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str | None = None
    no_us_listing: bool = False

    @model_validator(mode="after")
    def check_ticker_or_no_listing(self) -> "TickerExtraction":
        if self.no_us_listing:
            self.ticker = None  # ignore any ticker the model added anyway
            return self
        if self.ticker is None:
            raise ValueError("give a ticker, or set no_us_listing to true if the company has no US listing")
        self.ticker = MarketDataToolInput(ticker=self.ticker).ticker  # same rules as the tool input
        return self
