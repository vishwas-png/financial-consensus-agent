"""Eval harness tests: golden set validity, scoring math, and harness runs with a fake LLM."""

from evals.golden import load_fixture, load_golden_set
from src.core.nodes.context import CHUNK_ID_PATTERN

GOLDEN = load_golden_set()


# ---------- Golden set ----------

def test_golden_set_sizes() -> None:
    assert len(GOLDEN.extraction_cases) == 4
    assert len(GOLDEN.judge_cases) == 7
    assert len(GOLDEN.pipeline_cases) == 3
    all_ids = [c.id for c in GOLDEN.extraction_cases + GOLDEN.judge_cases + GOLDEN.pipeline_cases]
    assert len(all_ids) == len(set(all_ids)), "case ids must be unique"


def test_judge_cases_are_internally_consistent() -> None:
    for case in GOLDEN.judge_cases:
        if case.expected_is_grounded:
            assert case.planted_claims == [], case.id
            assert case.flaw in ("none", "opinion_only"), case.id
        else:
            assert case.planted_claims, case.id
            assert case.flaw not in ("none", "opinion_only"), case.id


def test_judge_case_citations_exist_in_fixture() -> None:
    for case in GOLDEN.judge_cases:
        fixture = load_fixture(case.fixture)
        ids = {CHUNK_ID_PATTERN.match(chunk).group(1) for chunk in fixture["filing_chunks"]}  # type: ignore[union-attr]
        assert set(case.analysis.filing_citations) <= ids, case.id


def test_planted_claims_appear_in_the_analysis_text() -> None:
    """A planted phrase must actually be in the analysis, or claim recall can never be scored."""
    for case in GOLDEN.judge_cases:
        text = " ".join([case.analysis.thesis, case.analysis.reasoning, *case.analysis.supporting_data_points]).lower()
        for phrase in case.planted_claims:
            assert phrase.lower() in text, (case.id, phrase)


def test_fixture_matches_the_numbers_the_labels_rely_on() -> None:
    """Guards against someone re-capturing the fixture without re-checking the labels."""
    data = load_fixture("aapl_snapshot.json")["raw_data"]
    assert round(data["valuation"]["trailing_pe"], 1) == 38.6
    assert data["valuation"]["dividend_yield_pct"] == 0.32
    assert data["financials"]["revenue_growth"] == 0.164
    annual = data["annual_financials"]
    assert round(annual[0]["total_revenue"] / annual[1]["total_revenue"] - 1, 3) == 0.064
