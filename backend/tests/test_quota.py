"""Quota tests: active-run limit, rolling daily limit, and per-user isolation."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.api.quota import check_quota
from src.db.models import ResearchRun, RunStatus, User
from tests.api_helpers import memory_session_factory

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db() -> Iterator[Session]:
    session = memory_session_factory()()
    session.add_all([User(id="alice", email="alice@example.com"), User(id="bob", email="bob@example.com")])
    session.commit()
    yield session
    session.close()


def add_runs(db: Session, user_id: str, count: int, status: RunStatus = RunStatus.DONE,
             hours_ago: float = 1.0) -> None:
    for _ in range(count):
        db.add(ResearchRun(user_id=user_id, ticker="AAPL", status=status,
                           created_at=NOW - timedelta(hours=hours_ago)))
    db.commit()


def assert_blocked(db: Session, user_id: str, message: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        check_quota(db, user_id, now=NOW, runs_per_day=5, max_active=1)
    assert excinfo.value.status_code == 429
    assert message in excinfo.value.detail


def test_new_user_is_allowed(db: Session) -> None:
    check_quota(db, "alice", now=NOW, runs_per_day=5, max_active=1)


def test_fifth_run_allowed_sixth_blocked(db: Session) -> None:
    add_runs(db, "alice", 4)
    check_quota(db, "alice", now=NOW, runs_per_day=5, max_active=1)  # 5th is fine
    add_runs(db, "alice", 1)
    assert_blocked(db, "alice", "Daily limit reached: 5/5")


def test_failed_runs_count_toward_daily_limit(db: Session) -> None:
    add_runs(db, "alice", 5, status=RunStatus.FAILED)
    assert_blocked(db, "alice", "Daily limit")


def test_runs_older_than_24_hours_do_not_count(db: Session) -> None:
    add_runs(db, "alice", 5, hours_ago=25)
    check_quota(db, "alice", now=NOW, runs_per_day=5, max_active=1)


@pytest.mark.parametrize("status", [RunStatus.PENDING, RunStatus.RUNNING])
def test_active_run_blocks_a_new_one(db: Session, status: RunStatus) -> None:
    add_runs(db, "alice", 1, status=status)
    assert_blocked(db, "alice", "in progress")


def test_other_users_runs_do_not_count(db: Session) -> None:
    add_runs(db, "bob", 5)
    add_runs(db, "bob", 1, status=RunStatus.RUNNING)
    check_quota(db, "alice", now=NOW, runs_per_day=5, max_active=1)
