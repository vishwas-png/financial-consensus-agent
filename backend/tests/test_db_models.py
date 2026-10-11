"""DB model tests on an in-memory SQLite database, including user-scoped reads."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

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
    count_active_runs,
    expire_overdue_runs,
    count_runs_since,
    get_events_for_run,
    get_run_for_user,
    get_runs_for_user,
    mark_stale_runs_failed,
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


# ---------- Stage 6: event, quota and maintenance helpers ----------

def add_events(db: Session, run: ResearchRun, steps: list[RunStep]) -> list[RunEvent]:
    events = [RunEvent(run_id=run.id, step=step, content={"n": i}) for i, step in enumerate(steps)]
    db.add_all(events)
    db.commit()
    return events


def test_events_for_run_are_ordered_and_resumable(db: Session) -> None:
    add_user(db, "alice")
    run = add_run(db, "alice")
    events = add_events(db, run, [RunStep.FETCH_DATA, RunStep.ANALYST, RunStep.CRITIC, RunStep.CONSENSUS_CHECK])
    assert [e.step for e in get_events_for_run(db, "alice", run.id)] == [
        RunStep.FETCH_DATA, RunStep.ANALYST, RunStep.CRITIC, RunStep.CONSENSUS_CHECK]
    after_second = get_events_for_run(db, "alice", run.id, after_id=events[1].id)
    assert [e.step for e in after_second] == [RunStep.CRITIC, RunStep.CONSENSUS_CHECK]


def test_user_cannot_read_another_users_events(db: Session) -> None:
    add_user(db, "alice")
    add_user(db, "bob")
    bob_run = add_run(db, "bob")
    add_events(db, bob_run, [RunStep.ANALYST])
    assert get_events_for_run(db, "alice", bob_run.id) == []
    assert len(get_events_for_run(db, "bob", bob_run.id)) == 1


def test_new_step_types_round_trip(db: Session) -> None:
    add_user(db, "alice")
    run = add_run(db, "alice")
    add_events(db, run, [RunStep.FETCH_DATA, RunStep.CONSENSUS_CHECK, RunStep.ERROR])
    db.expire_all()
    assert {e.step for e in get_events_for_run(db, "alice", run.id)} == {
        RunStep.FETCH_DATA, RunStep.CONSENSUS_CHECK, RunStep.ERROR}


def test_count_runs_since_uses_time_window_and_owner(db: Session) -> None:
    add_user(db, "alice")
    add_user(db, "bob")
    now = datetime.now(timezone.utc)
    old = add_run(db, "alice")
    old.created_at = now - timedelta(hours=30)
    add_run(db, "alice")
    add_run(db, "alice")
    add_run(db, "bob")
    db.commit()
    # SQLite stores datetimes without a timezone; the comparison must still work
    assert count_runs_since(db, "alice", now - timedelta(hours=24)) == 2
    assert count_runs_since(db, "alice", now - timedelta(hours=48)) == 3
    assert count_runs_since(db, "bob", now - timedelta(hours=24)) == 1


def test_count_active_runs(db: Session) -> None:
    add_user(db, "alice")
    pending, running, done, failed = (add_run(db, "alice") for _ in range(4))
    running.status = RunStatus.RUNNING
    done.status = RunStatus.DONE
    failed.status = RunStatus.FAILED
    db.commit()
    assert count_active_runs(db, "alice") == 2
    assert count_active_runs(db, "nobody") == 0


def test_mark_stale_runs_failed(db: Session) -> None:
    add_user(db, "alice")
    pending, running, done = (add_run(db, "alice") for _ in range(3))
    running.status = RunStatus.RUNNING
    done.status = RunStatus.DONE
    db.commit()

    assert mark_stale_runs_failed(db) == 2
    db.expire_all()
    assert db.get(ResearchRun, pending.id).status == RunStatus.FAILED  # type: ignore[union-attr]
    assert db.get(ResearchRun, running.id).status == RunStatus.FAILED  # type: ignore[union-attr]
    assert db.get(ResearchRun, done.id).status == RunStatus.DONE  # type: ignore[union-attr]
    error_events = get_events_for_run(db, "alice", running.id)
    assert [e.step for e in error_events] == [RunStep.ERROR]
    assert "restarted" in error_events[0].content["message"]
    assert mark_stale_runs_failed(db) == 0  # idempotent


def test_history_limit(db: Session) -> None:
    add_user(db, "alice")
    for _ in range(5):
        add_run(db, "alice")
    assert len(get_runs_for_user(db, "alice", limit=3)) == 3


def test_expire_overdue_runs_only_touches_this_users_old_active_runs(db: Session) -> None:
    add_user(db, "alice")
    add_user(db, "bob")
    now = datetime.now(timezone.utc)
    old_pending, old_running, old_done, fresh = (add_run(db, "alice") for _ in range(4))
    bobs_old = add_run(db, "bob")
    for run in (old_pending, old_running, old_done, bobs_old):
        run.created_at = now - timedelta(minutes=30)
    old_running.status = RunStatus.RUNNING
    old_done.status = RunStatus.DONE
    db.commit()

    assert expire_overdue_runs(db, "alice", created_before=now - timedelta(minutes=15)) == 2
    db.commit()
    db.expire_all()
    status = {run.id: db.get(ResearchRun, run.id).status for run in (old_pending, old_running, old_done, fresh, bobs_old)}  # type: ignore[union-attr]
    assert status == {old_pending.id: RunStatus.FAILED, old_running.id: RunStatus.FAILED,
                      old_done.id: RunStatus.DONE, fresh.id: RunStatus.PENDING, bobs_old.id: RunStatus.PENDING}
    events = get_events_for_run(db, "alice", old_running.id)
    assert [e.step for e in events] == [RunStep.ERROR] and "didn't finish in time" in events[0].content["message"]
