"""GraphState: the single object that flows through every LangGraph node.

Each node reads what it needs from the state and returns the fields it updates.
The state knows nothing about users; the API layer only passes in a ticker and
run settings such as max_iterations.
"""

from typing import Any

from pydantic import BaseModel, Field

from src.config import MAX_ITERATIONS
from src.core.schemas import AnalystOutput, AuditorVerdict, CriticFeedback


class GraphState(BaseModel):
    """Shared state for the fetch_data -> analyst <-> critic -> auditor graph."""

    ticker: str

    # Filled by fetch_data
    raw_data: dict[str, Any] = Field(default_factory=dict)
    filing_chunks: list[str] = Field(default_factory=list)

    # Filled by the agents (None until that node has run)
    analyst_output: AnalystOutput | None = None
    critic_feedback: CriticFeedback | None = None
    auditor_verdict: AuditorVerdict | None = None

    # Guardrail: completed Analyst/Critic rounds, and the max number of rounds in total
    iteration_count: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=MAX_ITERATIONS, ge=1)

    # True when the loop stopped because it hit max_iterations while the Critic still
    # disagreed. The result must then be shown as "unresolved disagreement", not consensus.
    forced_exit: bool = False
