"""SQLAlchemy ORM models: users, research_runs, run_events.

Rule: every read of research data is scoped by user_id. The helper functions at
the bottom of this file are the only way the API layer should load runs, so a
user can never see another user's runs.
"""

import enum
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


def utc_now() -> datetime:
    """Timezone-aware current time in UTC (used as a column default)."""
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    """Random UUID string, so run ids cannot be guessed from the stream URL."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RunStep(str, enum.Enum):
    """One saved event per graph step. The stream replays these, so the UI can show every stage."""

    FETCH_DATA = "fetch_data"            # market data + filing excerpts loaded (summary only)
    ANALYST = "analyst"
    CRITIC = "critic"
    CONSENSUS_CHECK = "consensus_check"  # round finished: iteration_count, forced_exit
    RISK_OFFICER = "risk_officer"        # Stage 9: material risks to the final thesis
    COMPARATOR = "comparator"            # Stage 9: side-by-side verdict of a comparison run
    AUDITOR = "auditor"
    ERROR = "error"                      # the run failed; content holds a user-safe message


class User(Base):
    __tablename__ = "users"

    # Google's stable account id (the ID token's "sub" claim)
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    runs: Mapped[list["ResearchRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_run_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The ticker, or for a comparison a label such as "AAPL vs MSFT"
    ticker: Mapped[str] = mapped_column(String(40))
    # Stage 9: "single" research run or "compare" (2-3 tickers in `tickers`)
    kind: Mapped[str] = mapped_column(String(16), default="single", server_default="single")
    tickers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Quota cost: 1 for a single run, one per ticker for a comparison
    units: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # native_enum=False stores the value as a plain string: works the same on SQLite and Postgres
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        default=RunStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Observability (Stage 9): step durations, LLM calls/tokens/retries, embedding calls
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    user: Mapped[User] = relationship(back_populates="runs")
    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunEvent.id"
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    step: Mapped[RunStep] = mapped_column(
        Enum(RunStep, native_enum=False, values_callable=lambda e: [m.value for m in e])
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[ResearchRun] = relationship(back_populates="events")


# ---------- User-scoped queries (the only way to read runs) ----------

def get_runs_for_user(db: Session, user_id: str, limit: int = 50) -> list[ResearchRun]:
    """This user's runs, newest first."""
    stmt = (
        select(ResearchRun)
        .where(ResearchRun.user_id == user_id)
        .order_by(ResearchRun.created_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_run_for_user(db: Session, user_id: str, run_id: str) -> ResearchRun | None:
    """One run, only if this user owns it. Returns None for other users' runs."""
    stmt = select(ResearchRun).where(
        ResearchRun.id == run_id, ResearchRun.user_id == user_id
    )
    return db.scalars(stmt).first()


def get_events_for_run(db: Session, user_id: str, run_id: str, after_id: int = 0) -> list[RunEvent]:
    """Events of one run, oldest first, only if this user owns the run.

    `after_id` lets the stream fetch only new events (and resume after a reconnect).
    The join on research_runs.user_id is what keeps other users' events out.
    """
    stmt = (
        select(RunEvent)
        .join(ResearchRun, RunEvent.run_id == ResearchRun.id)
        .where(RunEvent.run_id == run_id, ResearchRun.user_id == user_id, RunEvent.id > after_id)
        .order_by(RunEvent.id)
    )
    return list(db.scalars(stmt))


def count_runs_since(db: Session, user_id: str, since: datetime) -> int:
    """Quota units this user used at or after `since` (a comparison of N tickers counts N)."""
    stmt = select(func.coalesce(func.sum(ResearchRun.units), 0)).where(
        ResearchRun.user_id == user_id, ResearchRun.created_at >= since
    )
    return int(db.scalar(stmt) or 0)


def count_active_runs(db: Session, user_id: str) -> int:
    """How many of this user's runs are pending or running (for the concurrency quota)."""
    stmt = select(func.count()).select_from(ResearchRun).where(
        ResearchRun.user_id == user_id,
        ResearchRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING]),
    )
    return db.scalar(stmt) or 0


def _as_utc(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; ours are always stored in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def expire_overdue_runs(db: Session, user_id: str, now: datetime, max_age_seconds: float) -> int:
    """Mark this user's runs that are still pending/running but older than their limit as failed.
    The limit is `max_age_seconds` per quota unit (a 3-ticker comparison gets 3x). Caller commits.

    A healthy run ends (done or failed) within its timeout. One that is still "active" much
    later is stuck: a step that never returned, or a worker thread that was lost. Without this,
    it would block the user's active-run quota until the next restart.
    """
    active = db.scalars(select(ResearchRun).where(
        ResearchRun.user_id == user_id,
        ResearchRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING]),
    ))
    overdue = [run for run in active
               if _as_utc(run.created_at) < now - timedelta(seconds=max_age_seconds * max(run.units, 1))]
    for run in overdue:
        run.status = RunStatus.FAILED
        db.add(RunEvent(run_id=run.id, step=RunStep.ERROR,
                        content={"message": "This run didn't finish in time and was stopped. Please start it again."}))
    return len(overdue)


# ---------- Maintenance (not user-facing) ----------

def mark_stale_runs_failed(db: Session) -> int:
    """At startup: runs still pending/running belonged to a previous process that died.

    The in-process worker can't resume them, so mark them failed instead of leaving
    them "running" forever (which would also block the user's active-run quota).
    This is the only query that is not scoped by user_id; it is never exposed via the API.
    """
    stale = list(db.scalars(select(ResearchRun).where(
        ResearchRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING])
    )))
    for run in stale:
        run.status = RunStatus.FAILED
        db.add(RunEvent(run_id=run.id, step=RunStep.ERROR,
                        content={"message": "The server restarted before this run finished. Please start it again."}))
    db.commit()
    return len(stale)