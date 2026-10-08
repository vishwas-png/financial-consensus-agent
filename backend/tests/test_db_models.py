"""DB model tests on an in-memory SQLite database, including user-scoped reads."""

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import (
    ResearchRun,
    RunEvent,
    RunStatus,
    RunStep,
    User,
    get_run_for_user,
    get_runs_for_user,
)
from src.db.session import init_db, make_engine


@pytest.fixture
def db() -> Iterator[Session]:
    """A fresh in-memory database per test."""
    engine = make_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_user(db: Session, user_id: str) -> User:
    user = User(id=user_id, email=f"{user_id}@example.com")
    db.add(user)
    db.commit()
    return user


def add_run(db: Session, user_id: str, ticker: str = "AAPL") -> ResearchRun:
    run = ResearchRun(user_id=user_id, ticker=ticker)
    db.add(run)
    db.commit()
    return run


def test_run_defaults(db: Session) -> None:
    add_user(db, "alice")
    run = add_run(db, "alice")
    assert run.status == RunStatus.PENDING
    assert len(run.id) == 36  # UUID string
    assert run.created_at is not None
    assert run.final_output is None


def test_events_and_final_output_round_trip(db: Session) -> None:
    add_user(db, "alice")
    run = add_run(db, "alice")
    db.add(RunEvent(run_id=run.id, step=RunStep.ANALYST, content={"thesis": "Buy"}))
    db.add(RunEvent(run_id=run.id, step=RunStep.CRITIC, content={"agrees": True}))
    run.status = RunStatus.DONE
    run.final_output = {"verdict": {"is_grounded": True}}
    db.commit()
    db.expire_all()

    loaded = db.get(ResearchRun, run.id)
    assert loaded is not None
    assert loaded.status == RunStatus.DONE
    assert loaded.final_output == {"verdict": {"is_grounded": True}}
    assert [e.step for e in loaded.events] == [RunStep.ANALYST, RunStep.CRITIC]
    assert loaded.events[0].content == {"thesis": "Buy"}


def test_run_requires_existing_user(db: Session) -> None:
    db.add(ResearchRun(user_id="ghost", ticker="AAPL"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_duplicate_email_rejected(db: Session) -> None:
    db.add(User(id="a", email="same@example.com"))
    db.add(User(id="b", email="same@example.com"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_run_deletes_its_events(db: Session) -> None:
    add_user(db, "alice")
    run = add_run(db, "alice")
    db.add(RunEvent(run_id=run.id, step=RunStep.AUDITOR, content={}))
    db.commit()

    db.delete(run)
    db.commit()
    assert db.scalar(select(func.count()).select_from(RunEvent)) == 0


def test_user_only_sees_own_runs(db: Session) -> None:
    add_user(db, "alice")
    add_user(db, "bob")
    alice_run = add_run(db, "alice", "AAPL")
    bob_run = add_run(db, "bob", "MSFT")

    alice_runs = get_runs_for_user(db, "alice")
    assert [r.id for r in alice_runs] == [alice_run.id]
    assert [r.id for r in get_runs_for_user(db, "bob")] == [bob_run.id]


def test_user_cannot_read_another_users_run_by_id(db: Session) -> None:
    add_user(db, "alice")
    add_user(db, "bob")
    bob_run = add_run(db, "bob")

    assert get_run_for_user(db, "alice", bob_run.id) is None
    assert get_run_for_user(db, "bob", bob_run.id) is not None


def test_unknown_user_gets_no_runs(db: Session) -> None:
    add_user(db, "alice")
    add_run(db, "alice")
    assert get_runs_for_user(db, "mallory") == []
