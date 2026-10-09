"""EDGAR client tests with a mocked HTTP transport (no network)."""

from pathlib import Path

import httpx
import pytest

from src.rag.edgar import (
    EdgarClient,
    FilingConfigError,
    FilingDownloadError,
    FilingNotFoundError,
    FilingRef,
)

TICKER_MAP = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}
SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-Q", "10-K", "10-K"],
            "accessionNumber": ["a-1", "a-2", "0000320193-25-000079", "0000320193-24-000123"],
            "filingDate": ["2026-01-30", "2026-01-29", "2025-10-31", "2024-11-01"],
            "reportDate": ["2026-01-30", "2025-12-27", "2025-09-27", "2024-09-28"],
            "primaryDocument": ["x.htm", "q.htm", "aapl-20250927.htm", "aapl-20240928.htm"],
        }
    }
}
DOC_HTML = b"<html><body><p>Item 1A. Risk Factors</p></body></html>"


class MockSEC:
    """Records requests and serves canned EDGAR responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKER_MAP)
        if url.endswith("CIK0000320193.json"):
            return httpx.Response(200, json=SUBMISSIONS)
        if url.endswith("aapl-20250927.htm"):
            return httpx.Response(200, content=DOC_HTML)
        return httpx.Response(404)


@pytest.fixture
def sec() -> MockSEC:
    return MockSEC()


@pytest.fixture
def client(sec: MockSEC, tmp_path: Path) -> EdgarClient:
    http = httpx.Client(transport=httpx.MockTransport(sec.handler))
    return EdgarClient(user_agent="Test User test@example.com", cache_dir=tmp_path, http=http, sleep=lambda s: None)


def test_lookup_cik_including_class_shares(client: EdgarClient) -> None:
    assert client.lookup_cik("AAPL") == 320193
    assert client.lookup_cik("brk-b") == 1067983


def test_ticker_map_is_fetched_once(client: EdgarClient, sec: MockSEC) -> None:
    client.lookup_cik("AAPL")
    client.lookup_cik("BRK-B")
    assert sum("company_tickers" in str(r.url) for r in sec.requests) == 1


def test_unknown_ticker_raises_not_found(client: EdgarClient) -> None:
    with pytest.raises(FilingNotFoundError):
        client.lookup_cik("ZZZZQ")


def test_latest_filing_picks_newest_10k(client: EdgarClient) -> None:
    ref = client.latest_filing("AAPL")
    assert ref.accession == "0000320193-25-000079"
    assert ref.fiscal_year == 2025
    assert ref.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"


def test_missing_form_type_raises_not_found(client: EdgarClient) -> None:
    with pytest.raises(FilingNotFoundError):
        client.latest_filing("AAPL", form_type="20-F")


def test_every_request_sends_user_agent(client: EdgarClient, sec: MockSEC) -> None:
    client.download(client.latest_filing("AAPL"))
    assert sec.requests
    assert all(r.headers["User-Agent"] == "Test User test@example.com" for r in sec.requests)


def test_download_is_cached(client: EdgarClient, sec: MockSEC, tmp_path: Path) -> None:
    ref = client.latest_filing("AAPL")
    first = client.download(ref)
    count = len(sec.requests)
    second = client.download(ref)
    assert first == second and first.read_bytes() == DOC_HTML
    assert first.parent == tmp_path / "AAPL"
    assert len(sec.requests) == count  # no new request


def test_http_error_becomes_download_error(client: EdgarClient) -> None:
    ref = FilingRef("AAPL", 320193, "0000320193-25-000999", "10-K", "2025-10-31", "2025-09-27", "missing.htm")
    with pytest.raises(FilingDownloadError):
        client.download(ref)


def test_requests_are_throttled(sec: MockSEC, tmp_path: Path) -> None:
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(sec.handler))
    client = EdgarClient(user_agent="T t@example.com", cache_dir=tmp_path, http=http, sleep=sleeps.append)
    client.latest_filing("AAPL")  # two quick requests: ticker map, then submissions
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.15


def test_missing_user_agent_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(FilingConfigError):
        EdgarClient(user_agent=None, cache_dir=tmp_path)
