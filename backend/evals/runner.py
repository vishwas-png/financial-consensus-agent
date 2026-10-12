"""Runs single eval cases and returns plain result records. Shared by run_evals and judge_calibration.

Every runner takes the LLM client (and data sources) as parameters, so the harness itself
can be tested offline with fakes.
"""

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import yfinance as yf

from evals.golden import (
    CompareExtractionCase,
    DataCheckCase,
    ExtractionCase,
    JudgeCase,
    PipelineCase,
    RiskCase,
    load_fixture,
)
from evals.metrics import claim_hit, pipeline_checks
from src.clients.groq_client import GroqClient, LLMError
from src.core.data_checker import check_data_point, known_values
from src.core.graph import run_research
from src.core.nodes.auditor import make_auditor_node
from src.core.nodes.context import chunk_ids
from src.core.nodes.risk_officer import make_risk_officer_node
from src.core.nodes.fetch_data import FilingRetriever, default_filing_retriever
from src.core.schemas import AnalystOutput, CriticFeedback
from src.core.state import GraphState
from src.core.structured_output import ToolCallError
from src.core.tool_calling import NoUSListingError, extract_ticker, extract_tickers
from src.tools.market_data import MarketDataError, fetch_market_data


@dataclass
class ExtractionResult:
    case_id: str
    expected: str | None
    got: str | None
    error: str | None
    passed: bool
    seconds: float


@dataclass
class JudgeResult:
    case_id: str
    flaw: str
    expected_is_grounded: bool
    predicted_is_grounded: bool | None   # None = the auditor call itself failed
    unsupported_claims: list[str]
    planted_hits: dict[str, bool]        # planted phrase -> did the auditor name it?
    notes: str
    error: str | None
    seconds: float

    @property
    def correct(self) -> bool:
        return self.predicted_is_grounded == self.expected_is_grounded


@dataclass
class PipelineResult:
    case_id: str
    ticker: str | None
    checks: dict[str, bool] = field(default_factory=dict)
    rounds: int | None = None
    forced_exit: bool | None = None
    auditor_grounded: bool | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.error is None and bool(self.checks) and all(self.checks.values())


@dataclass
class CompareExtractionResult:
    case_id: str
    expected: list[str] | None
    got: list[str] | None
    error: str | None
    passed: bool
    seconds: float


@dataclass
class DataCheckResult:
    case_id: str
    expected: str
    got: str

    @property
    def passed(self) -> bool:
        return self.got == self.expected


@dataclass
class RiskResult:
    case_id: str
    checks: dict[str, bool] = field(default_factory=dict)
    overall_risk: str | None = None
    risks: list[str] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.error is None and bool(self.checks) and all(self.checks.values())


def to_dict(result: Any) -> dict[str, Any]:
    """Result dataclass -> JSON-friendly dict (including computed properties)."""
    data = asdict(result)
    for prop in ("correct", "passed"):
        if hasattr(result, prop):
            data[prop] = getattr(result, prop)
    return data


# ---------- Extraction ----------

def run_extraction_case(
    case: ExtractionCase,
    client: GroqClient,
    market_fetcher: Callable[[str], Any] = fetch_market_data,
) -> ExtractionResult:
    """expected_ticker set: the extracted ticker must match exactly.
    expected_ticker None: the request must FAIL cleanly, either at extraction (ToolCallError) or
    because the ticker the model produced does not exist (MarketDataError)."""
    start = time.monotonic()
    got: str | None = None
    error: str | None = None
    try:
        got = extract_ticker(case.request, client).ticker
        if case.expected_ticker is None:
            market_fetcher(got)  # an invented ticker should not exist
    except (ToolCallError, MarketDataError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except LLMError as exc:
        error = f"{type(exc).__name__}: {exc}"
        return ExtractionResult(case.id, case.expected_ticker, got, error, False, time.monotonic() - start)

    if case.expected_ticker is None:
        passed = error is not None
    else:
        passed = error is None and got == case.expected_ticker
    return ExtractionResult(case.id, case.expected_ticker, got, error, passed, time.monotonic() - start)


# ---------- Judge ----------

def run_judge_case(case: JudgeCase, client: GroqClient) -> JudgeResult:
    """Run the real auditor node on a hand-written analysis + frozen evidence."""
    fixture = load_fixture(case.fixture)
    state = GraphState(
        ticker=fixture["ticker"],
        raw_data=fixture["raw_data"],
        filing_chunks=fixture["filing_chunks"],
        analyst_output=case.analysis,
    )
    start = time.monotonic()
    try:
        verdict = make_auditor_node(client)(state)["auditor_verdict"]
    except (ToolCallError, LLMError) as exc:
        return JudgeResult(case.id, case.flaw, case.expected_is_grounded, None, [], {}, "",
                           f"{type(exc).__name__}: {exc}", time.monotonic() - start)
    hits = {phrase: claim_hit(phrase, verdict.unsupported_claims) for phrase in case.planted_claims}
    return JudgeResult(case.id, case.flaw, case.expected_is_grounded, verdict.is_grounded,
                       verdict.unsupported_claims, hits, verdict.notes, None, time.monotonic() - start)


# ---------- Pipeline ----------

def run_pipeline_case(
    case: PipelineCase,
    client: GroqClient,
    ticker_factory: Callable[[str], Any] = yf.Ticker,
    filing_retriever: FilingRetriever = default_filing_retriever,
) -> PipelineResult:
    """Full live path: extract ticker -> debate -> auditor, then code checks on the final state."""
    start = time.monotonic()
    try:
        ticker = extract_ticker(case.request, client).ticker
        final = run_research(ticker, max_iterations=case.max_iterations, client=client,
                             ticker_factory=ticker_factory, filing_retriever=filing_retriever)
    except Exception as exc:  # report any failure as a failed case instead of crashing the whole run
        return PipelineResult(case.id, None, error=f"{type(exc).__name__}: {exc}", seconds=time.monotonic() - start)

    verdict = final.auditor_verdict
    return PipelineResult(
        case_id=case.id,
        ticker=ticker,
        checks=pipeline_checks(final, ticker, case.expected_ticker, case.expected_sector),
        rounds=final.iteration_count,
        forced_exit=final.forced_exit,
        auditor_grounded=verdict.is_grounded if verdict else None,
        unsupported_claims=verdict.unsupported_claims if verdict else [],
        seconds=time.monotonic() - start,
    )


# ---------- Data Checker (Stage 9, no LLM) ----------

def run_data_check_case(case: DataCheckCase) -> DataCheckResult:
    values = known_values(load_fixture(case.fixture)["raw_data"])
    return DataCheckResult(case.id, case.expected_status, check_data_point(case.data_point, values).status)


# ---------- Risk Officer (Stage 9, live) ----------

def run_risk_case(case: RiskCase, analysis: AnalystOutput, client: GroqClient) -> RiskResult:
    """Run the real risk_officer node on frozen evidence; check citations and focus in code."""
    fixture = load_fixture(case.fixture)
    critic = CriticFeedback(agrees=not case.forced_exit, objections=case.critic_objections, missing_context=[])
    state = GraphState(ticker=fixture["ticker"], raw_data=fixture["raw_data"], filing_chunks=fixture["filing_chunks"],
                       analyst_output=analysis, critic_feedback=critic, iteration_count=1,
                       forced_exit=case.forced_exit)
    start = time.monotonic()
    try:
        assessment = make_risk_officer_node(client)(state)["risk_assessment"]
    except (ToolCallError, LLMError) as exc:
        return RiskResult(case.id, error=f"{type(exc).__name__}: {exc}", seconds=time.monotonic() - start)
    valid_ids = chunk_ids(state)
    text = " ".join([r.risk for r in assessment.key_risks] + assessment.what_would_change_the_thesis).lower()
    checks = {
        "citations_valid": all(r.citation is None or r.citation in valid_ids for r in assessment.key_risks),
        "cites_the_filing": any(r.citation for r in assessment.key_risks),
        "on_topic": any(keyword.lower() in text for keyword in case.expect_any_keyword),
    }
    return RiskResult(case.id, checks, assessment.overall_risk, [r.risk for r in assessment.key_risks],
                      None, time.monotonic() - start)


# ---------- Comparison extraction (Stage 9, live) ----------

def run_compare_extraction_case(case: CompareExtractionCase, client: GroqClient) -> CompareExtractionResult:
    """expected_tickers set: exact list, in order. None: the request must be declined (NoUSListingError)."""
    start = time.monotonic()
    try:
        got = extract_tickers(case.request, client)
    except NoUSListingError as exc:
        return CompareExtractionResult(case.id, case.expected_tickers, None, f"NoUSListingError: {exc}",
                                       case.expected_tickers is None, time.monotonic() - start)
    except (ToolCallError, LLMError) as exc:
        return CompareExtractionResult(case.id, case.expected_tickers, None, f"{type(exc).__name__}: {exc}",
                                       False, time.monotonic() - start)
    return CompareExtractionResult(case.id, case.expected_tickers, got, None, got == case.expected_tickers,
                                   time.monotonic() - start)
