"""SQLAlchemy ORM models: users, research_runs, run_events.

Rule: every read of research data is scoped by user_id. The helper functions at
the bottom of this file are the only way the API layer should load runs, so a
user can never see another user's runs.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, select
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
    ANALYST = "analyst"
    CRITIC = "critic"
    AUDITOR = "auditor"


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
    ticker: Mapped[str] = mapped_column(String(16))
    # native_enum=False stores the value as a plain string: works the same on SQLite and Postgres
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        default=RunStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

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

def get_runs_for_user(db: Session, user_id: str) -> list[ResearchRun]:
    """All runs owned by this user, newest first."""
    stmt = (
        select(ResearchRun)
        .where(ResearchRun.user_id == user_id)
        .order_by(ResearchRun.created_at.desc())
    )
    return list(db.scalars(stmt))


def get_run_for_user(db: Session, user_id: str, run_id: str) -> ResearchRun | None:
    """One run, only if this user owns it. Returns None for other users' runs."""
    stmt = select(ResearchRun).where(
        ResearchRun.id == run_id, ResearchRun.user_id == user_id
    )
    return db.scalars(stmt).first()
