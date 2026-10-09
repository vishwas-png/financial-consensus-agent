"""Pure scoring functions for the eval harness (no LLM calls, fully unit-tested).

Convention for the judge: the POSITIVE class is "ungrounded" (the auditor flags a problem).
- precision = of the analyses the auditor flagged, how many really had a planted flaw
- recall    = of the analyses with a planted flaw, how many the auditor caught
A judge that flags everything has recall 1.0 but poor precision; one that flags nothing
has precision undefined (None) and recall 0.0.
"""

from dataclasses import dataclass

from src.core.nodes.context import chunk_ids
from src.core.state import GraphState


@dataclass(frozen=True)
class Confusion:
    tp: int  # flaw planted, auditor flagged it
    fp: int  # no flaw, auditor flagged anyway (false alarm)
    fn: int  # flaw planted, auditor missed it
    tn: int  # no flaw, auditor said grounded

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self) -> float | None:
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else None

    @property
    def recall(self) -> float | None:
        flawed = self.tp + self.fn
        return self.tp / flawed if flawed else None

    @property
    def accuracy(self) -> float | None:
        return (self.tp + self.tn) / self.total if self.total else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


def confusion(expected_ungrounded: list[bool], predicted_ungrounded: list[bool]) -> Confusion:
    """Build a confusion matrix. True = ungrounded (the positive class)."""
    if len(expected_ungrounded) != len(predicted_ungrounded):
        raise ValueError("expected and predicted must have the same length")
    pairs = list(zip(expected_ungrounded, predicted_ungrounded))
    return Confusion(
        tp=sum(e and p for e, p in pairs),
        fp=sum((not e) and p for e, p in pairs),
        fn=sum(e and (not p) for e, p in pairs),
        tn=sum((not e) and (not p) for e, p in pairs),
    )


def claim_hit(planted_phrase: str, unsupported_claims: list[str]) -> bool:
    """Did the auditor name the planted claim? Case-insensitive key-phrase match."""
    phrase = planted_phrase.lower()
    return any(phrase in claim.lower() for claim in unsupported_claims)


def claim_recall(hits: list[bool]) -> float | None:
    """Share of planted claims the auditor named explicitly (stricter than verdict recall)."""
    return sum(hits) / len(hits) if hits else None


def agreement_rate(runs_per_case: list[list[bool]]) -> float | None:
    """Share of cases where every repeat gave the same verdict (judge consistency)."""
    if not runs_per_case:
        return None
    return sum(len(set(runs)) == 1 for runs in runs_per_case) / len(runs_per_case)


def pipeline_checks(state: GraphState, extracted_ticker: str, expected_ticker: str,
                    expected_sector: str) -> dict[str, bool]:
    """Code-checked expectations for one full pipeline run. Every value should be True."""
    analysis = state.analyst_output
    critic = state.critic_feedback
    return {
        "ticker_correct": extracted_ticker == expected_ticker,
        "sector_correct": state.raw_data.get("profile", {}).get("sector") == expected_sector,
        "filings_retrieved": len(state.filing_chunks) > 0,
        "analysis_present": analysis is not None,
        "citations_valid": analysis is not None and set(analysis.filing_citations) <= chunk_ids(state),
        "rounds_within_cap": 1 <= state.iteration_count <= state.max_iterations,
        "forced_exit_consistent": critic is not None and state.forced_exit == (not critic.agrees),
        "auditor_ran": state.auditor_verdict is not None,
    }


def fmt(value: float | None) -> str:
    """Format a metric for tables: 0.857 -> '0.86', None -> 'n/a'."""
    return "n/a" if value is None else f"{value:.2f}"
