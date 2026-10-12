"""Per-run observability: what a run cost and where its time went.

The worker opens a collector for each run (`run_context`). Code deep inside the run, such as
the Groq client and the embedder, reports to "the current run" through context variables, so
neither `core/` nor the clients need a run id or user id passed in. Outside a run (CLI, evals,
tests) there is no collector and reporting is a no-op.

Log lines written during a run get a ` [run=<first 8 chars>]` tag (RunIdLogFilter), so the
hosting provider's log search can show one run's lines.

Threads: LangGraph runs nodes in worker threads with a copy of the caller's context, and
fetch_data submits ingestion with `copy_context()`, so both see the same collector. A lock
keeps concurrent updates safe.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunMetrics:
    """Counters for one run. Only numbers and short labels: never prompts, keys or user data."""

    llm_calls: int = 0
    llm_retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_by_model: dict[str, int] = field(default_factory=dict)
    embedding_calls: int = 0
    embedded_texts: int = 0
    filing_ingest: dict[str, Any] | None = None   # set only when a 10-K was indexed during the run
    steps: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add_llm_call(self, model: str, prompt_tokens: int, completion_tokens: int, retries: int) -> None:
        with self._lock:
            self.llm_calls += 1
            self.llm_retries += retries
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.tokens_by_model[model] = self.tokens_by_model.get(model, 0) + prompt_tokens + completion_tokens

    def add_llm_retry(self) -> None:
        """A retry of a call that never succeeded (the successful case is counted in add_llm_call)."""
        with self._lock:
            self.llm_retries += 1

    def add_embedding_call(self, texts: int) -> None:
        with self._lock:
            self.embedding_calls += 1
            self.embedded_texts += texts

    def set_filing_ingest(self, **details: Any) -> None:
        with self._lock:
            self.filing_ingest = details

    def add_step(self, step: str, seconds: float, ticker: str | None = None) -> None:
        with self._lock:
            entry: dict[str, Any] = {"step": step, "seconds": round(seconds, 2)}
            if ticker:
                entry["ticker"] = ticker
            self.steps.append(entry)

    def as_dict(self, total_seconds: float | None = None) -> dict[str, Any]:
        """JSON-safe snapshot, stored in research_runs.metrics."""
        with self._lock:
            return {
                "total_seconds": None if total_seconds is None else round(total_seconds, 1),
                "llm_calls": self.llm_calls,
                "llm_retries": self.llm_retries,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "tokens_by_model": dict(self.tokens_by_model),
                "embedding_calls": self.embedding_calls,
                "embedded_texts": self.embedded_texts,
                "filing_ingest": self.filing_ingest,
                "steps": list(self.steps),
            }


_current_metrics: ContextVar[RunMetrics | None] = ContextVar("run_metrics", default=None)
_current_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)


def current_metrics() -> RunMetrics | None:
    """The collector of the run executing in this context, or None outside a run."""
    return _current_metrics.get()


@contextmanager
def run_context(run_id: str, metrics: RunMetrics) -> Iterator[RunMetrics]:
    """Make `metrics` and `run_id` "current" for everything this run executes."""
    metrics_token = _current_metrics.set(metrics)
    run_token = _current_run_id.set(run_id)
    try:
        yield metrics
    finally:
        _current_metrics.reset(metrics_token)
        _current_run_id.reset(run_token)


class Stopwatch:
    """Seconds since creation (monotonic), for timing phases."""

    def __init__(self) -> None:
        self.started = time.monotonic()

    def seconds(self) -> float:
        return time.monotonic() - self.started


class RunIdLogFilter(logging.Filter):
    """Adds `run_tag` to every record: " [run=1a2b3c4d]" during a run, "" otherwise."""

    def filter(self, record: logging.LogRecord) -> bool:
        run_id = _current_run_id.get()
        record.run_tag = f" [run={run_id[:8]}]" if run_id else ""
        return True
