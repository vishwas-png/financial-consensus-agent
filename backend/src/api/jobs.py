"""Background run worker: executes the research graph and persists every step.

Why not run the graph inside the SSE request? A debate takes minutes. If it ran in the
request, closing the browser tab would kill it and a reconnect couldn't resume. Instead
the worker writes each step to run_events, and the stream endpoint reads them back.

The worker is a small thread pool inside the API process (MAX_CONCURRENT_RUNS at once,
because of Groq free-tier limits). Runs left unfinished by a crash/restart are marked
failed at startup (see db.models.mark_stale_runs_failed).

core/ never sees user_id: the worker passes only the ticker and settings.
"""

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from typing import Any

from langgraph.errors import GraphRecursionError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.clients.groq_client import LLMError, LLMUnavailableError
from src.config import MAX_CONCURRENT_RUNS, RUN_TIMEOUT_SECONDS
from src.core.comparison import COMPARE_ROUNDS, stream_comparison
from src.core.graph import stream_research
from src.core.schemas import ComparisonOutput
from src.core.state import GraphState
from src.core.structured_output import ToolCallError
from src.db.models import ResearchRun, RunEvent, RunStatus, RunStep
from src.observability import RunMetrics, Stopwatch, run_context
from src.tools.market_data import DataSourceError, MarketDataError, TickerNotFoundError

logger = logging.getLogger(__name__)

# stream_research-compatible: (ticker, max_iterations=...) -> iterator of (node_name, state_update)
GraphRunner = Callable[..., Iterator[tuple[str, dict[str, Any]]]]
# stream_comparison-compatible: (tickers, max_iterations=...) -> iterator of (ticker | None, node, update)
ComparisonRunner = Callable[..., Iterator[tuple[str | None, str, dict[str, Any]]]]
TaggedSteps = Iterator[tuple[str | None, str, dict[str, Any]]]


# ---------- What gets stored ----------

def _jsonable(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def event_content(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """The JSON saved (and streamed) for one graph step."""
    if node == "fetch_data":
        # Summary only: the full price history and excerpt texts go into final_output once
        raw = update.get("raw_data", {})
        chunks = update.get("filing_chunks", [])
        return {
            "company": raw.get("profile", {}).get("name"),
            "price": raw.get("price", {}).get("current"),
            "currency": raw.get("profile", {}).get("currency"),
            "filing_excerpts": len(chunks),
            "filing_status": update.get("filing_status"),
            "filing_ids": [chunk.split("]")[0].lstrip("[") for chunk in chunks],
        }
    return {key: _jsonable(value) for key, value in update.items()}


def final_output(state: GraphState) -> dict[str, Any]:
    """The run's result as stored in research_runs.final_output (and shown in history)."""
    raw = state.raw_data
    return {
        "ticker": state.ticker,
        "outcome": "unresolved_disagreement" if state.forced_exit else "consensus",
        "rounds": state.iteration_count,
        "max_iterations": state.max_iterations,
        "forced_exit": state.forced_exit,
        "analyst_output": _jsonable(state.analyst_output),
        "critic_feedback": _jsonable(state.critic_feedback),
        "auditor_verdict": _jsonable(state.auditor_verdict),
        "risk_assessment": _jsonable(state.risk_assessment),
        "data_check": _jsonable(state.data_check),
        "filing_chunks": state.filing_chunks,
        "market_snapshot": {key: raw.get(key) for key in ("profile", "price", "valuation", "financials")},
    }


def user_message(error: Exception, ticker: str) -> str:
    """A safe, helpful message for the UI. Never a stack trace or internal detail."""
    if isinstance(error, TickerNotFoundError):
        return f"No market data was found for {ticker}. Check the company or ticker and try again."
    if isinstance(error, DataSourceError):
        return "The market data source is unavailable right now. Please try again later."
    if isinstance(error, MarketDataError):
        return f"Market data for {ticker} could not be loaded."
    if isinstance(error, LLMUnavailableError):
        return "The AI service is busy (rate limited). Please try again in a few minutes."
    if isinstance(error, ToolCallError):
        return "An AI agent produced invalid output twice, so the run was stopped."
    if isinstance(error, LLMError):
        return "The AI service returned an error. Please try again later."
    if isinstance(error, GraphRecursionError):
        return "The debate hit a safety limit and was stopped."
    if isinstance(error, RunTimeoutError):
        return "The research took too long (the AI service may be very busy) and was stopped. Please try again later."
    return "Something went wrong while running the research. Please try again."


@dataclass
class RunProgress:
    """What the worker has rebuilt from the steps so far: one GraphState per ticker (key None
    for a single run) and, for a comparison, the comparator's output."""

    states: dict[str | None, GraphState] = field(default_factory=dict)
    comparison: ComparisonOutput | None = None


def comparison_final_output(tickers: list[str], progress: RunProgress) -> dict[str, Any]:
    """research_runs.final_output for a comparison run."""
    per_ticker = {ticker: final_output(progress.states[ticker]) for ticker in tickers}
    return {
        "kind": "compare",
        "tickers": tickers,
        "comparison": _jsonable(progress.comparison),
        "per_ticker": per_ticker,
        # All excerpts in one list, so the UI resolves citations the same way as for single runs
        "filing_chunks": [chunk for ticker in tickers for chunk in per_ticker[ticker]["filing_chunks"]],
    }


class RunTimeoutError(Exception):
    """The run exceeded RUN_TIMEOUT_SECONDS."""


# ---------- Worker ----------

def _tag_single(steps: Iterator[tuple[str, dict[str, Any]]]) -> TaggedSteps:
    """A single run's steps as (None, node, update). Closing this also closes the graph."""
    try:
        for node, update in steps:
            yield None, node, update
    finally:
        close = getattr(steps, "close", None)
        if close is not None:
            close()


class RunWorker:
    """Runs research jobs in a bounded thread pool. Inject runners / `executor` / `clock` in tests."""

    def __init__(
        self,
        session_factory: sessionmaker,
        run_graph: GraphRunner = stream_research,
        executor: Executor | None = None,
        max_concurrent: int = MAX_CONCURRENT_RUNS,
        run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        run_comparison: ComparisonRunner = stream_comparison,
    ) -> None:
        self._session_factory = session_factory
        self._run_graph = run_graph
        self._run_comparison = run_comparison
        self._executor = executor or ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="research-run")
        self._run_timeout = run_timeout_seconds
        self._clock = clock

    def submit(self, run_id: str, ticker: str, max_iterations: int) -> Future:
        """Queue a run. Returns immediately; the run stays 'pending' until a thread is free."""
        return self._executor.submit(self.execute, run_id, ticker, max_iterations)

    def submit_comparison(self, run_id: str, tickers: list[str]) -> Future:
        """Queue a comparison run (Stage 9)."""
        return self._executor.submit(self.execute_comparison, run_id, tickers)

    def execute(self, run_id: str, ticker: str, max_iterations: int) -> None:
        """Run the graph for one ticker, saving each step and the run's metrics. Never raises."""
        self._execute(
            run_id, [ticker], self._run_timeout,
            start=lambda: _tag_single(self._run_graph(ticker, max_iterations=max_iterations)),
            new_state=lambda _: GraphState(ticker=ticker, max_iterations=max_iterations),
            finish=lambda progress: final_output(progress.states[None]),
        )

    def execute_comparison(self, run_id: str, tickers: list[str]) -> None:
        """Research each ticker, then compare them. Never raises. The time limit scales with N."""
        self._execute(
            run_id, tickers, self._run_timeout * len(tickers),
            start=lambda: self._run_comparison(tickers, max_iterations=COMPARE_ROUNDS),
            new_state=lambda ticker: GraphState(ticker=ticker, max_iterations=COMPARE_ROUNDS),
            finish=lambda progress: comparison_final_output(tickers, progress),
        )

    def _execute(
        self,
        run_id: str,
        tickers: list[str],
        timeout: float,
        start: Callable[[], TaggedSteps],
        new_state: Callable[[str | None], GraphState],
        finish: Callable[[RunProgress], dict[str, Any]],
    ) -> None:
        """Shared skeleton: mark running, stream + save steps, store the result or a safe error."""
        metrics = RunMetrics()
        label = " vs ".join(tickers)
        with run_context(run_id, metrics), self._session_factory() as db:
            run = db.get(ResearchRun, run_id)
            if run is None:
                logger.error("Run %s vanished before it started", run_id)
                return
            run.status = RunStatus.RUNNING
            db.commit()
            clock = Stopwatch()
            logger.info("Run %s started: %s", run_id, label)
            progress = RunProgress()
            try:
                self._stream_and_save(db, run_id, start, new_state, progress, metrics, timeout)
                run.final_output = finish(progress)
                run.metrics = metrics.as_dict(clock.seconds())
                run.status = RunStatus.DONE
                db.commit()
                logger.info("Run %s done in %.0fs: llm_calls=%d tokens=%d", run_id, clock.seconds(),
                            metrics.llm_calls, metrics.prompt_tokens + metrics.completion_tokens)
            except Exception as error:  # the job must always end in a recorded state
                logger.exception("Run %s (%s) failed after %.0fs", run_id, label, clock.seconds())
                db.rollback()
                db.refresh(run)
                run.metrics = metrics.as_dict(clock.seconds())  # failed runs cost tokens too
                if run.status != RunStatus.FAILED:  # FAILED already = expired by the API, with its event
                    failing = _failing_ticker(tickers, progress)
                    db.add(RunEvent(run_id=run_id, step=RunStep.ERROR,
                                    content={"message": user_message(error, failing), "type": type(error).__name__}))
                    run.status = RunStatus.FAILED
                db.commit()

    def _stream_and_save(
        self,
        db: Session,
        run_id: str,
        start: Callable[[], TaggedSteps],
        new_state: Callable[[str | None], GraphState],
        progress: RunProgress,
        metrics: RunMetrics,
        timeout: float,
    ) -> None:
        """Save one event per step and rebuild each ticker's final state from the updates.

        The deadline is checked between steps, because a thread can't be killed mid-call.
        LLM steps are bounded (Groq request timeout x retry attempts), but fetch_data's filing
        download + embedding is not, so a very slow step can overrun. expire_stuck_runs in the
        API covers that case for the user; this loop then notices and stops at the next step.
        """
        deadline = self._clock() + timeout
        steps = start()
        step_clock = Stopwatch()
        try:
            for ticker, node, update in steps:
                metrics.add_step(node, step_clock.seconds(), ticker)  # time since the previous step
                if node == "comparator":
                    progress.comparison = update["comparison"]
                else:
                    state = progress.states.get(ticker) or new_state(ticker)
                    progress.states[ticker] = state.model_copy(update=update)
                content = event_content(node, update)
                if ticker is not None:
                    content = {"ticker": ticker, **content}  # comparison steps say which company
                db.add(RunEvent(run_id=run_id, step=RunStep(node), content=content))
                db.commit()  # commit per step so the stream can show it immediately
                if self._clock() > deadline:
                    raise RunTimeoutError(f"Run exceeded {timeout:.0f}s")
                expired = db.scalar(select(ResearchRun.status).where(ResearchRun.id == run_id)) == RunStatus.FAILED
                db.commit()  # end that read transaction: the next step can run for minutes (see auth.py)
                if expired:
                    # The API expired this run while a slow step was running; don't revive it
                    raise RunTimeoutError("Run was expired while a step was still running")
                step_clock = Stopwatch()
        finally:
            close = getattr(steps, "close", None)
            if close is not None:
                close()  # stop the graph generator so it doesn't start another LLM call

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _failing_ticker(tickers: list[str], progress: RunProgress) -> str:
    """The ticker that was being researched when the run failed (for the error message)."""
    for ticker in tickers:
        state = progress.states.get(ticker if len(tickers) > 1 else None)
        if state is None or state.auditor_verdict is None:
            return ticker
    return " vs ".join(tickers)  # every ticker finished: the comparator step failed
