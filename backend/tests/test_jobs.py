"""Run worker tests with a fake graph (no LLM, no network)."""

import json
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import pytest
from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import sessionmaker

from src.api.jobs import RunWorker, user_message
from src.clients.groq_client import LLMUnavailableError
from src.core.schemas import AnalystOutput, AuditorVerdict, CriticFeedback
from src.core.structured_output import ToolCallError
from src.db.models import ResearchRun, RunEvent, RunStatus, RunStep, User
from src.tools.market_data import DataSourceError, TickerNotFoundError
from tests.api_helpers import memory_session_factory

ANALYSIS = AnalystOutput(thesis="Fairly valued.", supporting_data_points=["pe = 38.6"], reasoning="r",
                         confidence="medium", filing_citations=["AAPL-10K-2025-1A-003"])
DISAGREE = CriticFeedback(agrees=False, objections=["weak"], missing_context=[])
VERDICT = AuditorVerdict(is_grounded=True, unsupported_claims=[], notes="ok")
CHUNK = "[AAPL-10K-2025-1A-003] (Item 1A - Risk Factors) Suppliers."
RAW = {"profile": {"name": "Apple Inc.", "currency": "USD"}, "price": {"current": 336.9},
       "valuation": {"trailing_pe": 38.6}, "financials": {}, "price_history": [{"date": "2025-01-01", "close": 1.0}]}


def fake_graph(ticker: str, max_iterations: int = 3) -> Iterator[tuple[str, dict[str, Any]]]:
    """One round ending in a forced exit (max_iterations=1), then the auditor."""
    yield "fetch_data", {"raw_data": RAW, "filing_chunks": [CHUNK]}
    yield "analyst", {"analyst_output": ANALYSIS}
    yield "critic", {"critic_feedback": DISAGREE}
    yield "consensus_check", {"iteration_count": 1, "forced_exit": True}
    yield "auditor", {"auditor_verdict": VERDICT}


class InlineExecutor:
    """Runs submitted jobs immediately, in the calling thread."""

    def submit(self, fn, *args, **kwargs) -> Future:  # type: ignore[no-untyped-def]
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture
def session_factory() -> sessionmaker:
    factory = memory_session_factory()
    with factory() as db:
        db.add(User(id="alice", email="alice@example.com"))
        db.commit()
    return factory


def new_run(session_factory: sessionmaker) -> str:
    with session_factory() as db:
        run = ResearchRun(user_id="alice", ticker="AAPL")
        db.add(run)
        db.commit()
        return run.id


def load(session_factory: sessionmaker, run_id: str) -> tuple[ResearchRun, list[RunEvent]]:
    with session_factory() as db:
        run = db.get(ResearchRun, run_id)
        events = list(db.query(RunEvent).filter(RunEvent.run_id == run_id).order_by(RunEvent.id))
        return run, events  # type: ignore[return-value]


def test_successful_run_saves_every_step_and_final_output(session_factory: sessionmaker) -> None:
    run_id = new_run(session_factory)
    RunWorker(session_factory, run_graph=fake_graph, executor=InlineExecutor()).submit(run_id, "AAPL", 1)

    run, events = load(session_factory, run_id)
    assert run.status == RunStatus.DONE
    assert [e.step for e in events] == [RunStep.FETCH_DATA, RunStep.ANALYST, RunStep.CRITIC,
                                        RunStep.CONSENSUS_CHECK, RunStep.AUDITOR]
    # fetch_data is a summary, not the full data
    assert events[0].content == {"company": "Apple Inc.", "price": 336.9, "currency": "USD",
                                 "filing_excerpts": 1, "filing_ids": ["AAPL-10K-2025-1A-003"]}
    assert events[1].content["analyst_output"]["thesis"] == "Fairly valued."
    assert events[3].content == {"iteration_count": 1, "forced_exit": True}

    output = run.final_output
    assert output["outcome"] == "unresolved_disagreement" and output["rounds"] == 1
    assert output["auditor_verdict"] == {"is_grounded": True, "unsupported_claims": [], "notes": "ok"}
    assert output["filing_chunks"] == [CHUNK]
    assert "price_history" not in output["market_snapshot"]
    json.dumps(output)  # stored as JSON


def test_consensus_outcome(session_factory: sessionmaker) -> None:
    def agreeing_graph(ticker: str, max_iterations: int = 3) -> Iterator[tuple[str, dict[str, Any]]]:
        yield "consensus_check", {"iteration_count": 1, "forced_exit": False}

    run_id = new_run(session_factory)
    RunWorker(session_factory, run_graph=agreeing_graph, executor=InlineExecutor()).submit(run_id, "AAPL", 3)
    assert load(session_factory, run_id)[0].final_output["outcome"] == "consensus"


def test_failure_keeps_earlier_events_and_records_safe_error(session_factory: sessionmaker) -> None:
    def failing_graph(ticker: str, max_iterations: int = 3) -> Iterator[tuple[str, dict[str, Any]]]:
        yield "fetch_data", {"raw_data": RAW, "filing_chunks": []}
        raise LLMUnavailableError("Groq failed after 4 attempts: 429 secret-ish internal details")

    run_id = new_run(session_factory)
    RunWorker(session_factory, run_graph=failing_graph, executor=InlineExecutor()).submit(run_id, "AAPL", 3)

    run, events = load(session_factory, run_id)
    assert run.status == RunStatus.FAILED
    assert run.final_output is None
    assert [e.step for e in events] == [RunStep.FETCH_DATA, RunStep.ERROR]
    assert events[1].content["type"] == "LLMUnavailableError"
    assert "rate limited" in events[1].content["message"]
    assert "429" not in events[1].content["message"]  # internal detail not exposed


def test_unknown_node_name_fails_the_run_cleanly(session_factory: sessionmaker) -> None:
    def odd_graph(ticker: str, max_iterations: int = 3) -> Iterator[tuple[str, dict[str, Any]]]:
        yield "mystery_node", {}

    run_id = new_run(session_factory)
    RunWorker(session_factory, run_graph=odd_graph, executor=InlineExecutor()).submit(run_id, "AAPL", 3)
    assert load(session_factory, run_id)[0].status == RunStatus.FAILED


def test_graph_receives_ticker_and_max_iterations_but_no_user(session_factory: sessionmaker) -> None:
    received: list[tuple] = []

    def recording_graph(*args: Any, **kwargs: Any) -> Iterator[tuple[str, dict[str, Any]]]:
        received.append((args, kwargs))
        return iter(())

    run_id = new_run(session_factory)
    RunWorker(session_factory, run_graph=recording_graph, executor=InlineExecutor()).submit(run_id, "AAPL", 2)
    assert received == [(("AAPL",), {"max_iterations": 2})]  # core/ never sees user_id


def test_real_thread_pool(session_factory: sessionmaker) -> None:
    worker = RunWorker(session_factory, run_graph=fake_graph, executor=ThreadPoolExecutor(max_workers=1))
    run_id = new_run(session_factory)
    worker.submit(run_id, "AAPL", 1).result(timeout=10)
    assert load(session_factory, run_id)[0].status == RunStatus.DONE
    worker.shutdown()


@pytest.mark.parametrize(("error", "expected"), [
    (TickerNotFoundError("x"), "No market data was found for AAPL"),
    (DataSourceError("x"), "market data source is unavailable"),
    (LLMUnavailableError("x"), "rate limited"),
    (ToolCallError("x"), "invalid output twice"),
    (GraphRecursionError("x"), "safety limit"),
    (KeyError("internal"), "Something went wrong"),
])
def test_user_messages(error: Exception, expected: str) -> None:
    assert expected in user_message(error, "AAPL")
