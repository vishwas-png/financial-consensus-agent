"""Research endpoints: start a run, stream it live (SSE), read results and history.

Every endpoint requires a signed-in user, and every read goes through the user-scoped
helpers in db/models.py. Another user's run id returns 404 (not 403), so the API
doesn't even confirm that the run exists.
"""

import json
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.api.auth import get_current_user
from src.api.deps import get_db
from src.api.quota import QUOTA_WINDOW, check_quota, lock_user_for_quota
from src.clients.groq_client import LLMError, LLMUnavailableError
from src.config import MAX_ACTIVE_RUNS_PER_USER, MAX_ITERATIONS, MAX_USER_ITERATIONS, RUNS_PER_DAY
from src.core.structured_output import ToolCallError
from src.db.models import (
    ResearchRun,
    RunEvent,
    RunStatus,
    User,
    count_active_runs,
    count_runs_since,
    get_events_for_run,
    get_run_for_user,
    get_runs_for_user,
)

router = APIRouter()

FINISHED = (RunStatus.DONE, RunStatus.FAILED)
HEARTBEAT_SECONDS = 15.0


# ---------- Request / response models ----------

class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200, description='e.g. "How is Apple doing?" or "MSFT"')
    max_iterations: int = Field(default=MAX_ITERATIONS, ge=1, le=MAX_USER_ITERATIONS)


class RunCreated(BaseModel):
    run_id: str
    ticker: str
    status: str


class EventOut(BaseModel):
    id: int
    step: str
    content: dict[str, Any]
    timestamp: datetime


class RunDetail(BaseModel):
    id: str
    ticker: str
    status: str
    created_at: datetime
    final_output: dict[str, Any] | None
    events: list[EventOut]


class RunSummary(BaseModel):
    id: str
    ticker: str
    status: str
    created_at: datetime
    outcome: str | None
    thesis: str | None


def _event_out(event: RunEvent) -> EventOut:
    return EventOut(id=event.id, step=event.step.value, content=event.content, timestamp=event.timestamp)


# ---------- Endpoints ----------

@router.post("/research", status_code=status.HTTP_202_ACCEPTED, response_model=RunCreated)
def start_research(
    body: ResearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RunCreated:
    """Check quota, identify the ticker, create the run and start it in the background."""
    check_quota(db, user.id)  # fast fail BEFORE any LLM call
    db.commit()  # end this read transaction; don't keep it open during the slow LLM call

    try:
        ticker: str = request.app.state.ticker_extractor(body.query)
    except ToolCallError as error:
        # No run is created, so a failed extraction costs the user no quota
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Couldn't identify a single US-listed company in your request. Try a company name or ticker.",
        ) from error
    except LLMUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The AI service is busy. Please try again in a minute.") from error
    except LLMError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="The AI service returned an error. Please try again.") from error

    # Check again and insert atomically: another request from this user may have started a run
    # while we were extracting the ticker. The row lock makes those requests take turns.
    lock_user_for_quota(db, user.id)
    check_quota(db, user.id)
    run = ResearchRun(user_id=user.id, ticker=ticker)
    db.add(run)
    db.commit()  # releases the lock
    request.app.state.worker.submit(run.id, ticker, body.max_iterations)
    return RunCreated(run_id=run.id, ticker=ticker, status=run.status.value)


@router.get("/research/{run_id}", response_model=RunDetail)
def get_research(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RunDetail:
    run = get_run_for_user(db, user.id, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    events = get_events_for_run(db, user.id, run_id)
    return RunDetail(id=run.id, ticker=run.ticker, status=run.status.value, created_at=run.created_at,
                     final_output=run.final_output, events=[_event_out(e) for e in events])


def _sse(event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    """Format one Server-Sent Event."""
    lines = [f"id: {event_id}"] if event_id is not None else []
    lines += [f"event: {event}", f"data: {json.dumps(data, default=str)}"]
    return "\n".join(lines) + "\n\n"


@router.get("/research/{run_id}/stream")
def stream_research_events(
    run_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Live stream of a run's events (SSE), ending with an `end` event.

    The browser reconnects with `Last-Event-ID` to resume where it left off. The token is
    sent in the Authorization header, so the frontend reads this with fetch(), not EventSource.
    """
    session_factory = request.app.state.session_factory
    poll_seconds: float = request.app.state.stream_poll_seconds
    max_seconds: float = request.app.state.stream_max_seconds
    user_id = user.id

    with session_factory() as db:
        if get_run_for_user(db, user_id, run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    after_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    def generate() -> Iterator[str]:
        nonlocal after_id
        started = last_sent = time.monotonic()
        while True:
            with session_factory() as db:
                # Read the status BEFORE the events: the worker commits every event before
                # marking the run finished, so a finished status guarantees we see all events.
                run_status = get_run_for_user(db, user_id, run_id).status  # type: ignore[union-attr]
                events = get_events_for_run(db, user_id, run_id, after_id)
            for event in events:
                yield _sse(event.step.value, _event_out(event).model_dump(mode="json"), event.id)
                after_id = event.id
                last_sent = time.monotonic()
            if run_status in FINISHED and not events:
                yield _sse("end", {"status": run_status.value})
                return
            if time.monotonic() - started > max_seconds:
                yield _sse("end", {"status": "timeout"})  # safety net if a run is stuck
                return
            if time.monotonic() - last_sent > HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"  # comment line keeps proxies from closing an idle stream
                last_sent = time.monotonic()
            time.sleep(poll_seconds)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history", response_model=list[RunSummary])
def history(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[RunSummary]:
    """The signed-in user's runs, newest first."""
    summaries = []
    for run in get_runs_for_user(db, user.id):
        output = run.final_output or {}
        summaries.append(RunSummary(
            id=run.id, ticker=run.ticker, status=run.status.value, created_at=run.created_at,
            outcome=output.get("outcome"), thesis=(output.get("analyst_output") or {}).get("thesis"),
        ))
    return summaries


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Who is signed in, and how much quota is left."""
    since = datetime.now(timezone.utc) - QUOTA_WINDOW
    return {
        "id": user.id,
        "email": user.email,
        "quota": {
            "runs_last_24h": count_runs_since(db, user.id, since),
            "runs_per_day": RUNS_PER_DAY,
            "active_runs": count_active_runs(db, user.id),
            "max_active_runs": MAX_ACTIVE_RUNS_PER_USER,
            "window_hours": int(QUOTA_WINDOW / timedelta(hours=1)),
        },
    }
