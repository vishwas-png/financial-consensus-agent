"""Builds the evidence block both agents see: market data JSON + SEC filing excerpts.

Analyst and critic get exactly the same evidence, so the critic can check every
number and citation the analyst uses.
"""

import json
import re

from src.core.state import GraphState

# Excerpts are formatted as "[AAPL-10K-2025-1A-007] (Item 1A - Risk Factors) text..."
CHUNK_ID_PATTERN = re.compile(r"^\[([^\]]+)\]")


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
        f"Market data (JSON):\n{json.dumps(state.raw_data, indent=1)}\n\n"
        f"{filings}"
    )
