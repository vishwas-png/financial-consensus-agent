"""Minimal SEC EDGAR client: find and download a company's latest 10-K.

EDGAR rules we follow (https://www.sec.gov/os/accessing-edgar-data):
- every request sends a User-Agent with a name and contact email (SEC_USER_AGENT);
- at most 10 requests per second (we wait MIN_INTERVAL_SECONDS between requests).

Downloads are cached in FILINGS_DIR so the same filing is fetched only once.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.config import FILINGS_DIR, SEC_USER_AGENT

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{document}"
MIN_INTERVAL_SECONDS = 0.15  # stays under SEC's 10 requests/second limit


class FilingError(Exception):
    """Base class for SEC filing errors."""


class FilingConfigError(FilingError):
    """SEC_USER_AGENT is missing."""


class FilingNotFoundError(FilingError):
    """The ticker is unknown to EDGAR, or the company has no filing of that form type."""


class FilingDownloadError(FilingError):
    """EDGAR could not be reached or returned an error."""


@dataclass(frozen=True)
class FilingRef:
    """Identifies one filing on EDGAR."""

    ticker: str
    cik: int
    accession: str          # e.g. "0000320193-25-000079"
    form_type: str          # e.g. "10-K"
    filing_date: str        # ISO date the filing was submitted
    report_date: str        # ISO date of the period end (fiscal year end for a 10-K)
    primary_document: str   # e.g. "aapl-20250927.htm"

    @property
    def fiscal_year(self) -> int:
        return int(self.report_date[:4])

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik,
            accession_no_dashes=self.accession.replace("-", ""),
            document=self.primary_document,
        )


class EdgarClient:
    """Fetches filings from EDGAR. Inject `http` (an httpx.Client) and `sleep` in tests."""

    def __init__(
        self,
        user_agent: str | None = SEC_USER_AGENT,
        cache_dir: Path = FILINGS_DIR,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent:
            raise FilingConfigError("SEC_USER_AGENT is not set. Add 'Your Name your@email' to backend/.env.")
        self._http = http or httpx.Client(timeout=30.0, follow_redirects=True)
        self._headers = {"User-Agent": user_agent}
        self._cache_dir = cache_dir
        self._sleep = sleep
        self._last_request_at = 0.0
        self._ticker_map: dict[str, int] | None = None

    # ---------- HTTP ----------

    def _get(self, url: str) -> httpx.Response:
        """GET with the SEC User-Agent and a small delay between requests."""
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)
        try:
            response = self._http.get(url, headers=self._headers)
            self._last_request_at = time.monotonic()
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise FilingDownloadError(f"EDGAR request failed for {url}: {error}") from error
        return response

    # ---------- Lookups ----------

    def lookup_cik(self, ticker: str) -> int:
        """Map a ticker (e.g. "BRK-B") to its SEC CIK number."""
        if self._ticker_map is None:
            rows: dict[str, Any] = self._get(TICKER_MAP_URL).json()
            self._ticker_map = {row["ticker"].upper(): int(row["cik_str"]) for row in rows.values()}
        cik = self._ticker_map.get(ticker.upper())
        if cik is None:
            raise FilingNotFoundError(f"EDGAR has no company with ticker {ticker}")
        return cik

    def latest_filing(self, ticker: str, form_type: str = "10-K") -> FilingRef:
        """Find the most recent filing of `form_type` (recent filings are listed newest first)."""
        cik = self.lookup_cik(ticker)
        submissions = self._get(SUBMISSIONS_URL.format(cik=f"{cik:010d}")).json()
        recent = submissions["filings"]["recent"]
        for i, form in enumerate(recent["form"]):
            if form == form_type:
                return FilingRef(
                    ticker=ticker.upper(),
                    cik=cik,
                    accession=recent["accessionNumber"][i],
                    form_type=form_type,
                    filing_date=recent["filingDate"][i],
                    report_date=recent["reportDate"][i],
                    primary_document=recent["primaryDocument"][i],
                )
        raise FilingNotFoundError(f"No {form_type} found on EDGAR for {ticker}")

    # ---------- Download ----------

    def download(self, filing: FilingRef) -> Path:
        """Download the filing's main document (cached on disk) and return its path."""
        path = self._cache_dir / filing.ticker / f"{filing.accession}_{filing.primary_document}"
        if path.exists():
            return path
        content = self._get(filing.url).content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path
