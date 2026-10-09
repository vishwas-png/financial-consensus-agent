"""Capture a frozen evidence snapshot (market data + 10-K excerpts) for the eval harness.

Judge cases in golden_set.json are written against this exact evidence, so they stay
valid when prices move or a new 10-K is filed. Re-capturing means re-checking every
judge case label by hand.

Usage:  python -m evals.capture_fixture AAPL
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.rag.retriever import get_filing_chunks
from src.tools.market_data import fetch_market_data

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def capture(ticker: str) -> Path:
    snapshot = {
        "ticker": ticker,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_data": fetch_market_data(ticker),
        "filing_chunks": get_filing_chunks(ticker),
    }
    FIXTURES_DIR.mkdir(exist_ok=True)
    path = FIXTURES_DIR / f"{ticker.lower()}_snapshot.json"
    path.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m evals.capture_fixture <TICKER>")
        sys.exit(2)
    print(f"Wrote {capture(sys.argv[1].upper())}")
