"""API tests: research flow, SSE streaming, user scoping, quota, errors, CORS, startup.

The app is built with fakes: in-memory DB, fake Google verifier, a fake ticker extractor
and a fake graph. The worker runs jobs inline, so a POST finishes the whole run.
"""

import json
import os
from collections.abc import Iterator
from concurrent.futures import Executor, Future
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from src.api.jobs import RunWorker
from src.clients.groq_client import LLMUnavailableError
from src.config import FRONTEND_ORIGIN, RUN_EXPIRY_SECONDS
from src.core.structured_output import ToolCallError
from src.core.tool_calling import NoUSListingError
from src.db.models import ResearchRun, RunStatus, User
from src.main import create_app
from tests.api_helpers import auth_header, fake_verifier, memory_session_factory
from tests.test_jobs import InlineExecutor, fake_comparison, fake_graph

ALICE, BOB = auth_header("alice"), auth_header("bob")


def fake_tickers_extractor(query: str) -> list[str]:
    if "Reliance" in query:
        raise NoUSListingError("foreign")
    if "unclear" in query:
        raise ToolCallError("only one ticker")
    return ["AAPL", "MSFT", "NVDA"] if "three" in query else ["AAPL", "MSFT"]


def fake_extractor(query: str) -> str:
    if "Reliance" in query:
        raise ToolCallError("no US ticker")
    if "busy" in query:
        raise LLMUnavailableError("429")
    return "AAPL"


class NeverRunsExecutor(Executor):
    """Accepts jobs but never runs them, so runs stay 'pending'."""

    def submit(self, fn, *args, **kwargs) -> Future:  # type: ignore[no-untyped-def]
        return Future()


def build_client(executor: Any = None, graph: Any = fake_graph, factory: sessionmaker | None = None,
                 **app_kwargs: Any) -> TestClient:
    factory = factory or memory_session_factory()
    worker = RunWorker(factory, run_graph=graph, executor=executor or InlineExecutor(), run_comparison=fake_comparison)
    app = create_app(session_factory=factory, token_verifier=fake_verifier, worker=worker,
                     ticker_extractor=fake_extractor, tickers_extractor=fake_tickers_extractor,
                     stream_poll_seconds=0.01, **app_kwargs)
    return TestClient(app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with build_client() as test_client:  # "with" runs the startup/shutdown lifespan
        yield test_client


def start(client: TestClient, headers: dict[str, str], query: str = "How is Apple doing?", **extra: Any) -> Any:
    return client.post("/research", json={"query": query, **extra}, headers=headers)


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse an SSE body into [{'id':..., 'event':..., 'data':...}] (comments skipped)."""
    events = []
    for block in body.strip().split("\n\n"):
        fields: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            fields[key] = json.loads(value) if key == "data" else value
        if fields:
            events.append(fields)
    return events


# ---------- Basics ----------

def test_health_needs_no_auth(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/research"), ("get", "/research/x"), ("get", "/research/x/stream"), ("get", "/history"), ("get", "/me"),
])
def test_every_endpoint_requires_auth(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path, **({"json": {"query": "AAPL"}} if method == "post" else {}))
    assert response.status_code == 401


# ---------- Research flow ----------

def test_start_research_returns_202_and_runs_in_background(client: TestClient) -> None:
    response = start(client, ALICE, max_iterations=1)
    assert response.status_code == 202
    body = response.json()
    assert body["ticker"] == "AAPL" and len(body["run_id"]) == 36

    detail = client.get(f"/research/{body['run_id']}", headers=ALICE).json()
    assert detail["status"] == "done"
    assert [e["step"] for e in detail["events"]] == ["fetch_data", "analyst", "critic", "consensus_check", "auditor"]
    assert detail["final_output"]["outcome"] == "unresolved_disagreement"
    assert detail["final_output"]["auditor_verdict"]["is_grounded"] is True


def test_stream_replays_events_in_order_and_ends(client: TestClient) -> None:
    run_id = start(client, ALICE).json()["run_id"]
    response = client.get(f"/research/{run_id}/stream", headers=ALICE)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    assert [e["event"] for e in events] == ["fetch_data", "analyst", "critic", "consensus_check", "auditor", "end"]
    ids = [int(e["id"]) for e in events[:-1]]
    assert ids == sorted(ids)
    assert events[1]["data"]["content"]["analyst_output"]["thesis"] == "Fairly valued."
    assert events[-1]["data"] == {"status": "done"}


def test_stream_resumes_after_last_event_id(client: TestClient) -> None:
    run_id = start(client, ALICE).json()["run_id"]
    all_events = parse_sse(client.get(f"/research/{run_id}/stream", headers=ALICE).text)
    second_id = all_events[1]["id"]
    resumed = parse_sse(client.get(f"/research/{run_id}/stream", headers=ALICE | {"Last-Event-ID": second_id}).text)
    assert [e["event"] for e in resumed] == ["critic", "consensus_check", "auditor", "end"]


def test_failed_run_streams_error_then_end(client: TestClient) -> None:
    def failing_graph(ticker: str, max_iterations: int = 3) -> Iterator[tuple[str, dict[str, Any]]]:
        raise LLMUnavailableError("internal 429 details")
        yield  # pragma: no cover

    with build_client(graph=failing_graph) as failing_client:
        run_id = start(failing_client, ALICE).json()["run_id"]
        events = parse_sse(failing_client.get(f"/research/{run_id}/stream", headers=ALICE).text)
    assert [e["event"] for e in events] == ["error", "end"]
    assert "rate limited" in events[0]["data"]["content"]["message"]
    assert events[-1]["data"] == {"status": "failed"}


def test_stream_times_out_if_run_never_finishes() -> None:
    with build_client(executor=NeverRunsExecutor(), stream_max_seconds=0.05) as stuck_client:
        run_id = start(stuck_client, ALICE).json()["run_id"]
        events = parse_sse(stuck_client.get(f"/research/{run_id}/stream", headers=ALICE).text)
    assert events == [{"event": "end", "data": {"status": "timeout"}}]


# ---------- User scoping ----------

def test_other_users_cannot_see_a_run(client: TestClient) -> None:
    run_id = start(client, ALICE).json()["run_id"]
    assert client.get(f"/research/{run_id}", headers=BOB).status_code == 404
    assert client.get(f"/research/{run_id}/stream", headers=BOB).status_code == 404
    assert client.get("/history", headers=BOB).json() == []
    assert client.get(f"/research/{run_id}", headers=ALICE).status_code == 200


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/research/00000000-0000-0000-0000-000000000000", headers=ALICE).status_code == 404


def test_history_lists_own_runs_newest_first(client: TestClient) -> None:
    first = start(client, ALICE).json()["run_id"]
    second = start(client, ALICE).json()["run_id"]
    start(client, BOB)
    history = client.get("/history", headers=ALICE).json()
    assert [h["id"] for h in history] == [second, first]
    assert history[0]["thesis"] == "Fairly valued." and history[0]["outcome"] == "unresolved_disagreement"


# ---------- Validation and extraction errors ----------

@pytest.mark.parametrize("body", [
    {"query": ""},
    {"query": "x" * 201},
    {"query": "AAPL", "max_iterations": 0},
    {"query": "AAPL", "max_iterations": 4},
    {"query": "AAPL", "user_id": "bob"},  # unknown fields are rejected
    {},
], ids=["empty", "too-long", "zero-iterations", "too-many-iterations", "extra-field", "missing-query"])
def test_invalid_requests_are_422(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/research", json=body, headers=ALICE).status_code == 422


def test_unidentifiable_company_is_422_and_uses_no_quota(client: TestClient) -> None:
    response = start(client, ALICE, query="What about Reliance Industries?")
    assert response.status_code == 422
    assert "US-listed company" in response.json()["detail"]
    assert client.get("/history", headers=ALICE).json() == []
    assert client.get("/me", headers=ALICE).json()["quota"]["runs_last_24h"] == 0


def test_llm_unavailable_during_extraction_is_503(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    response = start(client, ALICE, query="busy busy")
    assert response.status_code == 503
    assert "429" not in response.json()["detail"]  # internal details stay out of the response...
    assert "LLM unavailable: 429" in caplog.text   # ...but reach the server log for debugging


# ---------- Quota through the API ----------

def test_active_run_blocks_second_request() -> None:
    with build_client(executor=NeverRunsExecutor()) as pending_client:
        assert start(pending_client, ALICE).status_code == 202
        second = start(pending_client, ALICE)
        assert second.status_code == 429 and "in progress" in second.json()["detail"]
        assert start(pending_client, BOB).status_code == 202  # other users unaffected


def test_stuck_run_is_expired_instead_of_blocking_forever() -> None:
    # Found live: a run stuck in a slow step kept the user at "1 run in progress" indefinitely
    factory = memory_session_factory()
    with build_client(executor=NeverRunsExecutor(), factory=factory) as pending_client:
        stuck_id = start(pending_client, ALICE).json()["run_id"]
        assert start(pending_client, ALICE).status_code == 429  # a fresh run still blocks

        with factory() as db:  # age the stuck run past RUN_EXPIRY_SECONDS
            run = db.get(ResearchRun, stuck_id)
            run.created_at = datetime.now(timezone.utc) - timedelta(seconds=RUN_EXPIRY_SECONDS + 60)
            db.commit()

        assert pending_client.get("/me", headers=ALICE).json()["quota"]["active_runs"] == 0
        assert start(pending_client, ALICE).status_code == 202
        stuck = pending_client.get(f"/research/{stuck_id}", headers=ALICE).json()
        assert stuck["status"] == "failed"


def test_daily_limit_through_api(client: TestClient) -> None:
    for _ in range(5):
        assert start(client, ALICE).status_code == 202
    sixth = start(client, ALICE)
    assert sixth.status_code == 429 and "Daily limit" in sixth.json()["detail"]


def test_me_reports_quota(client: TestClient) -> None:
    start(client, ALICE)
    me = client.get("/me", headers=ALICE).json()
    assert me["id"] == "alice" and me["email"] == "alice@example.com"
    assert me["quota"]["runs_last_24h"] == 1 and me["quota"]["active_runs"] == 0
    assert me["quota"]["runs_per_day"] == 5


# ---------- Startup and CORS ----------

def test_startup_marks_interrupted_runs_failed() -> None:
    factory = memory_session_factory()
    with factory() as db:
        db.add(User(id="alice", email="alice@example.com"))
        db.add(ResearchRun(id="stale-run", user_id="alice", ticker="AAPL", status=RunStatus.RUNNING))
        db.commit()
    with build_client(factory=factory) as restarted:
        detail = restarted.get("/research/stale-run", headers=ALICE).json()
    assert detail["status"] == "failed"
    assert "restarted" in detail["events"][0]["content"]["message"]


def test_cors_allows_only_the_frontend_origin(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    allowed = client.options("/research", headers={"Origin": FRONTEND_ORIGIN, **preflight})
    assert allowed.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    blocked = client.options("/research", headers={"Origin": "https://evil.example.com", **preflight})
    assert "access-control-allow-origin" not in blocked.headers


# ---------- Live (opt-in: pytest -m live) ----------

@pytest.mark.live
def test_live_full_api_run(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Real extraction + debate + RAG + auditor through the API (only Google sign-in is faked).

    Uses a temporary SQLite FILE so the request thread and the worker thread get separate
    connections, like in production, and a real thread pool.
    """
    from concurrent.futures import ThreadPoolExecutor

    from src.db.session import init_db, make_engine, make_session_factory
    from src.main import extract_ticker_with_llm

    engine = make_engine(f"sqlite:///{tmp_path / 'live.db'}")
    init_db(engine)
    factory = make_session_factory(engine)
    worker = RunWorker(factory, executor=ThreadPoolExecutor(max_workers=1))
    app = create_app(session_factory=factory, token_verifier=fake_verifier, worker=worker,
                     ticker_extractor=extract_ticker_with_llm, stream_poll_seconds=0.5)
    with TestClient(app) as live_client:
        created = start(live_client, ALICE, query="How is Apple doing?", max_iterations=1)
        assert created.status_code == 202, created.text
        assert created.json()["ticker"] == "AAPL"
        events = parse_sse(live_client.get(f"/research/{created.json()['run_id']}/stream", headers=ALICE).text)

    steps = [e["event"] for e in events]
    assert steps == ["fetch_data", "analyst", "critic", "consensus_check", "auditor", "end"], steps
    assert events[-1]["data"] == {"status": "done"}
    assert events[0]["data"]["content"]["filing_excerpts"] > 0


# ---------- Quota race (Stage 8: check + insert must be atomic) ----------

def test_quota_rechecked_after_slow_extraction() -> None:
    """A run started by a parallel request while we extract the ticker must be counted."""
    factory = memory_session_factory()

    def extractor_with_parallel_request(query: str) -> str:
        # Simulates another request from the same user finishing first, during our LLM call
        with factory() as db:
            db.add(ResearchRun(user_id="alice", ticker="MSFT"))  # pending = active run
            db.commit()
        return "AAPL"

    worker = RunWorker(factory, run_graph=fake_graph, executor=NeverRunsExecutor())
    app = create_app(session_factory=factory, token_verifier=fake_verifier, worker=worker,
                     ticker_extractor=extractor_with_parallel_request, stream_poll_seconds=0.01)
    with TestClient(app) as race_client:
        response = start(race_client, ALICE)
        assert response.status_code == 429, response.text
        assert "in progress" in response.json()["detail"]
        assert [run["ticker"] for run in race_client.get("/history", headers=ALICE).json()] == ["MSFT"]


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="set TEST_POSTGRES_URL to run against Postgres")
def test_concurrent_requests_cannot_both_start_a_run_on_postgres() -> None:
    """Two truly simultaneous POSTs from one user: the row lock lets exactly one through."""
    import threading

    from alembic import command

    from src.config import normalize_database_url
    from src.db.session import make_engine, make_session_factory
    from tests.test_migrations import alembic_config

    url = normalize_database_url(os.environ["TEST_POSTGRES_URL"])
    command.downgrade(alembic_config(url), "base")
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)
    factory = make_session_factory(engine)

    both_extracting = threading.Barrier(2, timeout=10)

    def slow_extractor(query: str) -> str:
        both_extracting.wait()  # both requests have passed the first quota check
        return "AAPL"

    worker = RunWorker(factory, run_graph=fake_graph, executor=NeverRunsExecutor())
    app = create_app(session_factory=factory, token_verifier=fake_verifier, worker=worker,
                     ticker_extractor=slow_extractor, stream_poll_seconds=0.01)
    statuses: list[int] = []

    def post() -> None:
        with TestClient(app) as client:
            statuses.append(start(client, ALICE).status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    with factory() as db:
        runs = db.query(ResearchRun).filter(ResearchRun.user_id == "alice").count()
    engine.dispose()
    assert sorted(statuses) == [202, 429], statuses
    assert runs == 1


def test_company_without_us_listing_is_a_clear_422() -> None:
    from src.core.tool_calling import NoUSListingError

    def declining_extractor(query: str) -> str:
        raise NoUSListingError("no US listing")

    factory = memory_session_factory()
    app = create_app(session_factory=factory, token_verifier=fake_verifier,
                     worker=RunWorker(factory, run_graph=fake_graph, executor=InlineExecutor()),
                     ticker_extractor=declining_extractor)
    with TestClient(app) as client:
        response = start(client, ALICE, query="What do you think of Reliance Industries?")
        assert response.status_code == 422
        assert "doesn't appear to have a US stock listing" in response.json()["detail"]
        assert client.get("/history", headers=ALICE).json() == []  # no run, no quota used


def test_run_detail_includes_metrics(client: TestClient) -> None:
    run_id = start(client, ALICE).json()["run_id"]
    detail = client.get(f"/research/{run_id}", headers=ALICE).json()
    assert [step["step"] for step in detail["metrics"]["steps"]][0] == "fetch_data"
    assert set(detail["metrics"]) >= {"llm_calls", "prompt_tokens", "completion_tokens", "steps", "total_seconds"}



# ---------- Stage 9: POST /compare ----------

def compare(client: TestClient, headers: dict[str, str], query: str = "Apple vs Microsoft") -> Any:
    return client.post("/compare", json={"query": query}, headers=headers)


def test_compare_runs_and_streams_tagged_steps(client: TestClient) -> None:
    created = compare(client, ALICE)
    assert created.status_code == 202
    assert created.json()["ticker"] == "AAPL vs MSFT"
    run_id = created.json()["run_id"]

    detail = client.get(f"/research/{run_id}", headers=ALICE).json()
    assert detail["kind"] == "compare" and detail["tickers"] == ["AAPL", "MSFT"] and detail["status"] == "done"
    events = parse_sse(client.get(f"/research/{run_id}/stream", headers=ALICE).text)
    assert events[0]["data"]["content"]["ticker"] == "AAPL"
    assert [e["event"] for e in events][-2:] == ["comparator", "end"]

    summary = client.get("/history", headers=ALICE).json()[0]
    assert summary["kind"] == "compare" and summary["outcome"] == "prefers MSFT"


def test_compare_costs_one_unit_per_ticker(client: TestClient) -> None:
    compare(client, ALICE, "three companies")
    assert client.get("/me", headers=ALICE).json()["quota"]["runs_last_24h"] == 3
    compare(client, ALICE)                                   # 3 + 2 = 5 = the daily limit
    third = compare(client, ALICE)
    assert third.status_code == 429 and "This comparison needs 2" in third.json()["detail"]
    assert start(client, ALICE).status_code == 429           # a single run is over the limit too


def test_compare_needing_more_units_than_left_is_rejected(client: TestClient) -> None:
    for _ in range(3):
        assert start(client, ALICE).status_code == 202       # 3 used, 2 left
    assert compare(client, ALICE).status_code == 202          # 2 tickers fit exactly
    assert client.get("/me", headers=ALICE).json()["quota"]["runs_last_24h"] == 5


def test_three_tickers_rejected_when_only_two_units_left(client: TestClient) -> None:
    for _ in range(3):
        start(client, ALICE)
    response = compare(client, ALICE, "three companies")
    assert response.status_code == 429 and "needs 3" in response.json()["detail"]


@pytest.mark.parametrize(("query", "message"), [
    ("Apple vs Reliance", "US stock listing"),
    ("something unclear", "2 to 3 different US-listed companies"),
])
def test_compare_extraction_failures_are_422_and_free(client: TestClient, query: str, message: str) -> None:
    response = compare(client, ALICE, query)
    assert response.status_code == 422 and message in response.json()["detail"]
    assert client.get("/me", headers=ALICE).json()["quota"]["runs_last_24h"] == 0


def test_compare_requires_auth_and_valid_body(client: TestClient) -> None:
    assert client.post("/compare", json={"query": "Apple vs Microsoft"}).status_code == 401
    assert client.post("/compare", json={"query": ""}, headers=ALICE).status_code == 422
    assert client.post("/compare", json={"query": "x", "max_iterations": 2}, headers=ALICE).status_code == 422


def test_other_users_cannot_see_a_comparison(client: TestClient) -> None:
    run_id = compare(client, ALICE).json()["run_id"]
    assert client.get(f"/research/{run_id}", headers=BOB).status_code == 404
