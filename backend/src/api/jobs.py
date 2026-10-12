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
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from typing import Any

from langgraph.errors import GraphRecursionError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.clients.groq_client import LLMError, LLMUnavailableError
from src.config import MAX_CONCURRENT_RUNS, RUN_TIMEOUT_SECONDS
from src.core.graph import stream_research
from src.core.state import GraphState
from src.core.structured_output import ToolCallError
from src.db.models import ResearchRun, RunEvent, RunStatus, RunStep
from src.observability import RunMetrics, Stopwatch, run_context
from src.tools.market_data import DataSourceError, MarketDataError, TickerNotFoundError

logger = logging.getLogger(__name__)

# stream_research-compatible: (ticker, max_iterations=...) -> iterator of (node_name, state_update)
GraphRunner = Callable[..., Iterator[tuple[str, dict[str, Any]]]]


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


class RunTimeoutError(Exception):
    """The run exceeded RUN_TIMEOUT_SECONDS."""


# ---------- Worker ----------

class RunWorker:
    """Runs research jobs in a bounded thread pool. Inject `run_graph` / `executor` / `clock` in tests."""

    def __init__(
        self,
        session_factory: sessionmaker,
        run_graph: GraphRunner = stream_research,
        executor: Executor | None = None,
        max_concurrent: int = MAX_CONCURRENT_RUNS,
        run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._run_graph = run_graph
        self._executor = executor or ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="research-run")
        self._run_timeout = run_timeout_seconds
        self._clock = clock

    def submit(self, run_id: str, ticker: str, max_iterations: int) -> Future:
        """Queue a run. Returns immediately; the run stays 'pending' until a thread is free."""
        return self._executor.submit(self.execute, run_id, ticker, max_iterations)

    def execute(self, run_id: str, ticker: str, max_iterations: int) -> None:
        """Run the graph for one research run, saving each step and the run's metrics. Never raises."""
        metrics = RunMetrics()
        with run_context(run_id, metrics), self._session_factory() as db:
            run = db.get(ResearchRun, run_id)
            if run is None:
                logger.error("Run %s vanished before it started", run_id)
                return
            run.status = RunStatus.RUNNING
            db.commit()
            clock = Stopwatch()
            logger.info("Run %s started: %s, max_iterations=%d", run_id, ticker, max_iterations)
            try:
                state = self._stream_and_save(db, run_id, ticker, max_iterations, metrics)
                run.final_output = final_output(state)
                run.metrics = metrics.as_dict(clock.seconds())
                run.status = RunStatus.DONE
                db.commit()
                logger.info("Run %s done in %.0fs: rounds=%d forced_exit=%s llm_calls=%d tokens=%d",
                            run_id, clock.seconds(), state.iteration_count, state.forced_exit,
                            metrics.llm_calls, metrics.prompt_tokens + metrics.completion_tokens)
            except Exception as error:  # the job must always end in a recorded state
                logger.exception("Run %s (%s) failed after %.0fs", run_id, ticker, clock.seconds())
                db.rollback()
                db.refresh(run)
                run.metrics = metrics.as_dict(clock.seconds())  # failed runs cost tokens too
                if run.status != RunStatus.FAILED:  # FAILED already = expired by the API, with its event
                    db.add(RunEvent(run_id=run_id, step=RunStep.ERROR,
                                    content={"message": user_message(error, ticker), "type": type(error).__name__}))
                    run.status = RunStatus.FAILED
                db.commit()

    def _stream_and_save(self, db: Session, run_id: str, ticker: str, max_iterations: int,
                         metrics: RunMetrics) -> GraphState:
        """Save one event per graph step and rebuild the final state from the updates.

        The deadline is checked between steps, because a thread can't be killed mid-call.
        LLM steps are bounded (Groq request timeout x retry attempts), but fetch_data's filing
        download + embedding is not, so a very slow step can overrun. expire_stuck_runs in the
        API covers that case for the user; this loop then notices and stops at the next step.
        """
        deadline = self._clock() + self._run_timeout
        state = GraphState(ticker=ticker, max_iterations=max_iterations)
        steps = self._run_graph(ticker, max_iterations=max_iterations)
        step_clock = Stopwatch()
        try:
            for node, update in steps:
                metrics.add_step(node, step_clock.seconds())  # time from the previous step to this one
                state = state.model_copy(update=update)
                db.add(RunEvent(run_id=run_id, step=RunStep(node), content=event_content(node, update)))
                db.commit()  # commit per step so the stream can show it immediately
                if self._clock() > deadline:
                    raise RunTimeoutError(f"Run exceeded {self._run_timeout:.0f}s")
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
        return state

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
