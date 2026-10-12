"""Eval harness tests: golden set validity, scoring math, and harness runs with a fake LLM."""

import pytest

from evals.golden import load_fixture, load_golden_set
from evals.metrics import agreement_rate, claim_hit, claim_recall, confusion, fmt, pipeline_checks
from evals.run_evals import save_results, summarize
from evals.runner import run_extraction_case, run_judge_case, run_pipeline_case, to_dict
from src.core.nodes.analyst import ANALYST_TOOL_NAME
from src.core.nodes.auditor import AUDITOR_TOOL_NAME
from src.core.nodes.context import CHUNK_ID_PATTERN
from src.core.nodes.critic import CRITIC_TOOL_NAME
from src.core.schemas import AnalystOutput, AuditorVerdict, CriticFeedback
from src.core.state import GraphState
from src.core.tool_calling import MARKET_DATA_TOOL_NAME
from src.tools.market_data import TickerNotFoundError
from src.core.nodes.risk_officer import RISK_OFFICER_TOOL_NAME
from tests.fakes import ToolRoutingClient, low_risk_assessment
from tests.test_market_data import FakeTicker


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


# ---------- Metrics ----------


def test_confusion_counts_and_scores() -> None:
    # True = ungrounded. 3 flawed (2 caught), 2 clean (1 false alarm)
    matrix = confusion([True, True, True, False, False], [True, True, False, True, False])
    assert (matrix.tp, matrix.fp, matrix.fn, matrix.tn) == (2, 1, 1, 1)
    assert matrix.precision == pytest.approx(2 / 3)
    assert matrix.recall == pytest.approx(2 / 3)
    assert matrix.accuracy == pytest.approx(3 / 5)
    assert matrix.f1 == pytest.approx(2 / 3)


def test_confusion_undefined_scores_are_none() -> None:
    never_flags = confusion([True, False], [False, False])
    assert never_flags.precision is None  # nothing flagged
    assert never_flags.recall == 0.0
    assert never_flags.f1 is None
    assert confusion([False], [False]).recall is None  # no flawed cases at all


def test_confusion_length_mismatch() -> None:
    with pytest.raises(ValueError):
        confusion([True], [True, False])


def test_claim_hit_is_case_insensitive_substring() -> None:
    claims = ["Dividend yield of 32% -- dividend_yield_pct is 0.32%", "Other claim"]
    assert claim_hit("32%", claims)
    assert claim_hit("DIVIDEND YIELD", claims)
    assert not claim_hit("foldable", claims)
    assert claim_recall([True, False, True, True]) == 0.75
    assert claim_recall([]) is None


def test_agreement_rate() -> None:
    assert agreement_rate([[True, True], [False, True], [False, False]]) == pytest.approx(2 / 3)
    assert agreement_rate([]) is None


def test_fmt() -> None:
    assert fmt(0.8571) == "0.86" and fmt(None) == "n/a"


ANALYSIS = AnalystOutput(thesis="t", supporting_data_points=["x"], reasoning="r", confidence="low",
                         filing_citations=["AAPL-10K-2025-1A-003"])
CHUNKS = ["[AAPL-10K-2025-1A-003] (Item 1A - Risk Factors) Suppliers."]


def good_state() -> GraphState:
    return GraphState(
        ticker="AAPL", raw_data={"profile": {"sector": "Technology"}}, filing_chunks=CHUNKS,
        analyst_output=ANALYSIS, critic_feedback=CriticFeedback(agrees=True, objections=[], missing_context=[]),
        iteration_count=1, max_iterations=2, forced_exit=False,
        auditor_verdict=AuditorVerdict(is_grounded=True, unsupported_claims=[], notes="ok"),
    )


def test_pipeline_checks_all_pass_on_good_state() -> None:
    checks = pipeline_checks(good_state(), "AAPL", "AAPL", "Technology")
    assert all(checks.values()), checks


def test_pipeline_checks_catch_each_problem() -> None:
    bad = good_state().model_copy(update={
        "filing_chunks": [],                      # no filings -> also makes the citation invalid
        "forced_exit": True,                      # inconsistent: the critic agreed
        "auditor_verdict": None,
        "raw_data": {"profile": {"sector": "Energy"}},
    })
    checks = pipeline_checks(bad, "MSFT", "AAPL", "Technology")
    assert {name for name, ok in checks.items() if not ok} == {
        "ticker_correct", "sector_correct", "filings_retrieved", "citations_valid",
        "forced_exit_consistent", "auditor_ran",
    }


# ---------- Runners with a fake LLM ----------


def ticker_client(ticker: str) -> ToolRoutingClient:
    return ToolRoutingClient({MARKET_DATA_TOOL_NAME: lambda n: {"ticker": ticker}})


def grounded_verdict(n: int) -> AuditorVerdict:
    return AuditorVerdict(is_grounded=True, unsupported_claims=[], notes="ok")


def test_extraction_case_exact_match() -> None:
    case = GOLDEN.extraction_cases[0]  # Apple -> AAPL
    assert run_extraction_case(case, ticker_client("aapl")).passed
    assert not run_extraction_case(case, ticker_client("MSFT")).passed


def test_must_fail_case_passes_only_on_clean_failure() -> None:
    case = next(c for c in GOLDEN.extraction_cases if c.expected_ticker is None)

    def not_found(ticker: str) -> None:
        raise TickerNotFoundError(ticker)

    # Model invents a ticker that does not exist -> clean failure -> pass
    assert run_extraction_case(case, ticker_client("RELI"), market_fetcher=not_found).passed
    # Model invents a ticker that DOES exist -> wrong company -> fail
    assert not run_extraction_case(case, ticker_client("RELX"), market_fetcher=lambda t: {}).passed


def test_judge_case_scores_verdict_and_planted_claims() -> None:
    case = next(c for c in GOLDEN.judge_cases if c.id == "judge-unit-misread")
    catches = ToolRoutingClient({AUDITOR_TOOL_NAME: lambda n: AuditorVerdict(
        is_grounded=False, unsupported_claims=["dividend yield of 32% -- it is 0.32%"], notes="unit error")})
    result = run_judge_case(case, catches)
    assert result.correct and result.planted_hits == {"32%": True}

    result = run_judge_case(case, ToolRoutingClient({AUDITOR_TOOL_NAME: grounded_verdict}))
    assert not result.correct and result.planted_hits == {"32%": False}


def test_judge_case_sends_fixture_evidence_to_auditor() -> None:
    case = GOLDEN.judge_cases[0]
    client = ToolRoutingClient({AUDITOR_TOOL_NAME: grounded_verdict})
    run_judge_case(case, client)
    content = client.calls[0]["messages"][1]["content"]
    assert "[AAPL-10K-2025-7-015]" in content and case.analysis.thesis in content


def test_pipeline_case_end_to_end_with_fakes() -> None:
    case = GOLDEN.pipeline_cases[0]  # Apple, Technology
    client = ToolRoutingClient({
        MARKET_DATA_TOOL_NAME: lambda n: {"ticker": "AAPL"},
        ANALYST_TOOL_NAME: lambda n: ANALYSIS,
        CRITIC_TOOL_NAME: lambda n: CriticFeedback(agrees=True, objections=[], missing_context=[]),
        RISK_OFFICER_TOOL_NAME: low_risk_assessment,
        AUDITOR_TOOL_NAME: grounded_verdict,
    })
    result = run_pipeline_case(case, client, ticker_factory=lambda s: FakeTicker(), filing_retriever=lambda t: CHUNKS)
    assert result.passed, (result.checks, result.error)
    assert result.rounds == 1 and result.auditor_grounded is True


def test_pipeline_case_reports_errors_instead_of_crashing() -> None:
    case = GOLDEN.pipeline_cases[0]
    # The fake only knows the extraction tool, so the analyst call raises KeyError inside the run
    result = run_pipeline_case(case, ticker_client("AAPL"), ticker_factory=lambda s: FakeTicker(),
                               filing_retriever=lambda t: CHUNKS)
    assert not result.passed and result.error is not None


def test_summary_and_saved_results(tmp_path) -> None:  # type: ignore[no-untyped-def]
    case = GOLDEN.judge_cases[0]
    judge = [run_judge_case(case, ToolRoutingClient({AUDITOR_TOOL_NAME: grounded_verdict}))]
    extraction = [run_extraction_case(GOLDEN.extraction_cases[0], ticker_client("AAPL"))]
    summary = summarize(extraction, judge, [])
    assert summary["extraction"] == {"passed": 1, "total": 1}
    assert summary["judge"]["correct"] == 1 and summary["judge"]["confusion"]["tn"] == 1
    path = save_results(summary, {"judge": judge, "extraction": extraction}, results_dir=tmp_path)
    assert '"correct": true' in path.read_text(encoding="utf-8")
    assert to_dict(judge[0])["case_id"] == case.id


# ---------- Judge calibration ----------

def test_calibration_report_with_repeats_and_errors() -> None:
    from evals.judge_calibration import calibration_report, run_calibration
    from src.clients.groq_client import LLMUnavailableError

    # A judge that always says "grounded", except it errors on its very first call
    class FlakyGroundedClient(ToolRoutingClient):
        def chat(self, messages, model, tools=None, tool_choice=None, temperature=0.0):  # type: ignore[no-untyped-def]
            if not self.calls:
                self.calls.append({"tool": AUDITOR_TOOL_NAME, "messages": messages, "model": model})
                raise LLMUnavailableError("rate limited")
            return super().chat(messages, model, tools, tool_choice, temperature)

    client = FlakyGroundedClient({AUDITOR_TOOL_NAME: grounded_verdict})
    runs = run_calibration(GOLDEN, client, repeats=2)
    report = calibration_report(runs)

    flawed = sum(not c.expected_is_grounded for c in GOLDEN.judge_cases)
    clean = len(GOLDEN.judge_cases) - flawed
    assert report["errors"] == 1
    assert report["verdicts_scored"] == 2 * len(GOLDEN.judge_cases) - 1
    # Always "grounded": catches nothing (recall 0), never flags (precision undefined)
    assert report["confusion"]["tp"] == 0 and report["confusion"]["fp"] == 0
    assert report["confusion"]["fn"] + report["confusion"]["tn"] == report["verdicts_scored"]
    assert report["recall"] == 0.0 and report["precision"] is None
    assert report["agreement_rate"] == 1.0  # consistent, just wrong
    majority = [case["majority_correct"] for case in report["per_case"].values()]
    assert majority.count(True) == clean and majority.count(False) == flawed


def test_calibration_report_perfect_judge(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from evals.judge_calibration import calibration_report, run_calibration, save_report

    labels = {c.analysis.thesis: c for c in GOLDEN.judge_cases}

    class OracleClient(ToolRoutingClient):
        """Answers with the manual label, naming every planted claim."""

        def chat(self, messages, model, tools=None, tool_choice=None, temperature=0.0):  # type: ignore[no-untyped-def]
            content = messages[1]["content"]
            case = next(c for thesis, c in labels.items() if thesis in content)
            verdict = AuditorVerdict(
                is_grounded=case.expected_is_grounded,
                unsupported_claims=[f"{p} -- planted" for p in case.planted_claims],
                notes="oracle",
            )
            self.responders = {AUDITOR_TOOL_NAME: lambda n: verdict}
            return super().chat(messages, model, tools, tool_choice, temperature)

    runs = run_calibration(GOLDEN, OracleClient({}), repeats=1)
    report = calibration_report(runs)
    assert report["precision"] == 1.0 and report["recall"] == 1.0 and report["accuracy"] == 1.0
    assert report["claim_recall"] == 1.0
    path = save_report(report, runs, results_dir=tmp_path)
    assert path.name.startswith("calibration_") and path.exists()


# ---------- Stage 9: Data Checker and Risk Officer suites ----------

def test_data_check_cases_all_agree_with_manual_labels() -> None:
    # The Data Checker is deterministic, so its eval suite doubles as a regression test
    from evals.runner import run_data_check_case

    results = [run_data_check_case(case) for case in GOLDEN.data_check_cases]
    assert len(results) >= 10
    assert [r.case_id for r in results if not r.passed] == []


def test_risk_cases_reference_existing_judge_analyses() -> None:
    assert GOLDEN.risk_cases
    for case in GOLDEN.risk_cases:
        assert GOLDEN.judge_case(case.analysis_from).analysis.thesis


def test_risk_case_checks_with_a_fake_client() -> None:
    from evals.runner import run_risk_case

    case = GOLDEN.risk_cases[0]
    analysis = GOLDEN.judge_case(case.analysis_from).analysis
    good = {"key_risks": [{"risk": "Single-source suppliers could disrupt supply.", "severity": "high",
                           "citation": "AAPL-10K-2025-1A-023"}],
            "what_would_change_the_thesis": ["margins fall"], "overall_risk": "medium"}
    result = run_risk_case(case, analysis, ToolRoutingClient({RISK_OFFICER_TOOL_NAME: lambda n: good}))
    assert result.passed, result.checks

    off_topic = good | {"key_risks": [{"risk": "Weather.", "severity": "low", "citation": None}]}
    result = run_risk_case(case, analysis, ToolRoutingClient({RISK_OFFICER_TOOL_NAME: lambda n: off_topic}))
    assert result.checks == {"citations_valid": True, "cites_the_filing": False, "on_topic": False}


def test_compare_extraction_cases_with_a_fake_client() -> None:
    from evals.runner import run_compare_extraction_case
    from src.core.tool_calling import COMPARISON_TOOL_NAME

    by_id = {case.id: case for case in GOLDEN.compare_extraction_cases}
    right = ToolRoutingClient({COMPARISON_TOOL_NAME: lambda n: {"tickers": ["AAPL", "MSFT"]}})
    assert run_compare_extraction_case(by_id["compare-two-names"], right).passed

    reversed_order = ToolRoutingClient({COMPARISON_TOOL_NAME: lambda n: {"tickers": ["MSFT", "AAPL"]}})
    assert not run_compare_extraction_case(by_id["compare-two-names"], reversed_order).passed

    declines = ToolRoutingClient({COMPARISON_TOOL_NAME: lambda n: {"no_us_listing": True}})
    assert run_compare_extraction_case(by_id["compare-foreign"], declines).passed
    invents = ToolRoutingClient({COMPARISON_TOOL_NAME: lambda n: {"tickers": ["AAPL", "RIL"]}})
    assert not run_compare_extraction_case(by_id["compare-foreign"], invents).passed
