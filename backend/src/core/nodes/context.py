"""Builds the evidence block both agents see: market data JSON + SEC filing excerpts.

Analyst and critic get exactly the same evidence, so the critic can check every
number and citation the analyst uses.
"""

import json
import re
from typing import Any

from src.core.state import GraphState

# Excerpts are formatted as "[AAPL-10K-2025-1A-007] (Item 1A - Risk Factors) text..."
CHUNK_ID_PATTERN = re.compile(r"^\[([^\]]+)\]")

# Weekly closes are down-sampled to every Nth point in the prompt (~monthly). The full
# series stays in state.raw_data; this only saves tokens (Groq free tier: 8k tokens/minute).
PRICE_HISTORY_STEP = 4


def prompt_market_data(raw_data: dict[str, Any]) -> str:
    """Market data as compact JSON with a down-sampled price history (always keeps the last close)."""
    data = dict(raw_data)
    history = data.get("price_history") or []
    if history:
        sampled = history[::PRICE_HISTORY_STEP]
        if sampled[-1] != history[-1]:
            sampled.append(history[-1])
        data["price_history"] = sampled
    return json.dumps(data, separators=(",", ":"))


def chunk_ids(state: GraphState) -> set[str]:
    """Ids of the filing excerpts in state (the only ids the analyst may cite)."""
    ids = set()
    for chunk in state.filing_chunks:
        match = CHUNK_ID_PATTERN.match(chunk)
        if match:
            ids.add(match.group(1))
    return ids


def evidence_block(state: GraphState) -> str:
    """Ticker, market data and filing excerpts as one prompt section."""
    if state.filing_chunks:
        filings = "SEC filing excerpts (latest 10-K):\n\n" + "\n\n".join(state.filing_chunks)
    else:
        filings = "SEC filing excerpts: none available for this ticker."
    return (
        f"Ticker: {state.ticker}\n\n"
        f"Market data (JSON):\n{prompt_market_data(state.raw_data)}\n\n"
        f"{filings}"
    )
