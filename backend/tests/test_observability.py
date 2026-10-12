"""Per-run metrics: the collector, its propagation into graph/ingestion threads, and the log tag."""

import logging
from types import SimpleNamespace
from typing import Any

import groq
import httpx

from src.core.graph import stream_research
from src.observability import RunIdLogFilter, RunMetrics, current_metrics, run_context
from tests.test_groq_client import OK_MESSAGE, REQUEST, make_client
from tests.test_guardrail import debate_client
from tests.test_market_data import FakeTicker


def response_with_usage(prompt: int, completion: int) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=OK_MESSAGE)],
                           usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


# ---------- The collector ----------

def test_no_collector_outside_a_run() -> None:
    assert current_metrics() is None


def test_run_context_sets_and_resets_the_collector() -> None:
    metrics = RunMetrics()
    with run_context("run-1", metrics):
        assert current_metrics() is metrics
    assert current_metrics() is None


def test_counters_and_snapshot() -> None:
    metrics = RunMetrics()
    metrics.add_llm_call("openai/gpt-oss-120b", prompt_tokens=1000, completion_tokens=200, retries=0)
    metrics.add_llm_call("openai/gpt-oss-20b", prompt_tokens=300, completion_tokens=50, retries=0)
    metrics.add_llm_retry()
    metrics.add_embedding_call(96)
    metrics.add_step("fetch_data", 12.345, ticker="AAPL")
    metrics.set_filing_ingest(ticker="AAPL", chunks=139, total_seconds=29.0)

    snapshot = metrics.as_dict(total_seconds=95.04)
    assert snapshot == {
        "total_seconds": 95.0, "llm_calls": 2, "llm_retries": 1,
        "prompt_tokens": 1300, "completion_tokens": 250,
        "tokens_by_model": {"openai/gpt-oss-120b": 1200, "openai/gpt-oss-20b": 350},
        "embedding_calls": 1, "embedded_texts": 96,
        "filing_ingest": {"ticker": "AAPL", "chunks": 139, "total_seconds": 29.0},
        "steps": [{"step": "fetch_data", "seconds": 12.35, "ticker": "AAPL"}],
    }


# ---------- Groq client reports usage and retries ----------

def test_groq_client_reports_tokens_and_retries() -> None:
    rate_limited = groq.RateLimitError("429", response=httpx.Response(429, request=REQUEST), body=None)
    client, _, _ = make_client([rate_limited, response_with_usage(800, 120)])
    metrics = RunMetrics()
    with run_context("run-1", metrics):
        client.chat([], model="openai/gpt-oss-120b")
    assert (metrics.llm_calls, metrics.llm_retries) == (1, 1)
    assert (metrics.prompt_tokens, metrics.completion_tokens) == (800, 120)


def test_groq_client_without_usage_or_collector_is_fine() -> None:
    client, _, _ = make_client([SimpleNamespace(choices=[SimpleNamespace(message=OK_MESSAGE)])] * 2)
    client.chat([], model="m")  # no collector: no error
    metrics = RunMetrics()
    with run_context("run-1", metrics):
        client.chat([], model="m")  # response without usage: counted, 0 tokens
    assert (metrics.llm_calls, metrics.prompt_tokens) == (1, 0)


# ---------- Propagation into LangGraph's node threads and the ingestion thread ----------

class CountingClient:
    """Wraps the scripted debate client and reports each call like GroqClient does."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        metrics = current_metrics()
        assert metrics is not None, "node code must see the run's collector"
        metrics.add_llm_call("fake", prompt_tokens=10, completion_tokens=1, retries=0)
        return self.inner.chat(*args, **kwargs)


def test_collector_reaches_graph_nodes_and_ingestion_thread() -> None:
    seen_in_ingestion: list[RunMetrics | None] = []

    def retriever(ticker: str) -> list[str]:
        seen_in_ingestion.append(current_metrics())  # runs in fetch_data's ingestion thread
        return []

    metrics = RunMetrics()
    with run_context("run-1", metrics):
        steps = [node for node, _ in stream_research(
            "AAPL", max_iterations=1, client=CountingClient(debate_client(None)),
            ticker_factory=lambda s: FakeTicker(), filing_retriever=retriever)]
    assert seen_in_ingestion == [metrics]
    assert metrics.llm_calls == steps.count("analyst") + steps.count("critic") + steps.count("auditor")


# ---------- Log tag ----------

def make_record() -> logging.LogRecord:
    return logging.LogRecord("x", logging.INFO, __file__, 1, "message", None, None)


def test_log_lines_get_the_run_tag_only_during_a_run() -> None:
    log_filter = RunIdLogFilter()
    outside = make_record()
    log_filter.filter(outside)
    assert outside.run_tag == ""  # type: ignore[attr-defined]

    inside = make_record()
    with run_context("1a2b3c4d-5e6f-7a8b-9c0d", RunMetrics()):
        log_filter.filter(inside)
    assert inside.run_tag == " [run=1a2b3c4d]"  # type: ignore[attr-defined]

    formatted = logging.Formatter("%(name)s%(run_tag)s: %(message)s").format(inside)
    assert formatted == "x [run=1a2b3c4d]: message"
