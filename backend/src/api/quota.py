"""Per-user quota, checked BEFORE any LLM or graph work (each run costs real tokens).

Two limits, both counted from research_runs (no extra table):
- at most MAX_ACTIVE_RUNS_PER_USER runs pending/running at once;
- at most RUNS_PER_DAY runs started in the last 24 hours (rolling window).
Failed runs still count toward the daily limit: they consumed LLM calls too.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import MAX_ACTIVE_RUNS_PER_USER, RUN_EXPIRY_SECONDS, RUNS_PER_DAY
from src.db.models import User, count_active_runs, count_runs_since, expire_overdue_runs

QUOTA_WINDOW = timedelta(hours=24)


def check_quota(
    db: Session,
    user_id: str,
    now: datetime | None = None,
    runs_per_day: int = RUNS_PER_DAY,
    max_active: int = MAX_ACTIVE_RUNS_PER_USER,
    units: int = 1,
) -> None:
    """Raise HTTP 429 if this user may not start a run costing `units` right now.

    A single run costs 1 unit; a comparison costs one per ticker (Stage 9).
    """
    now = now or datetime.now(timezone.utc)

    if count_active_runs(db, user_id) >= max_active:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You already have {max_active} research run(s) in progress. Wait for it to finish.",
        )

    used = count_runs_since(db, user_id, now - QUOTA_WINDOW)
    if used + units > runs_per_day:
        needs = f" This comparison needs {units}." if units > 1 else ""
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily limit reached: {used}/{runs_per_day} research runs in the last 24 hours.{needs}",
        )


def expire_stuck_runs(db: Session, user_id: str, now: datetime | None = None) -> int:
    """Fail this user's runs that are far past the run timeout, then commit.

    Called before the quota check (and when the UI loads the quota) so a stuck run can't
    block the user's active-run limit forever. Kept out of check_quota on purpose:
    check_quota also runs under the row lock, and committing there would release it.
    """
    now = now or datetime.now(timezone.utc)
    expired = expire_overdue_runs(db, user_id, now=now, max_age_seconds=RUN_EXPIRY_SECONDS)
    if expired:
        db.commit()
    return expired


def lock_user_for_quota(db: Session, user_id: str) -> None:
    """Lock this user's row until the current transaction ends (Postgres: SELECT ... FOR UPDATE).

    Two simultaneous requests from the same user then run "check quota + insert run" one after
    the other, so both can't pass the active-run check (D43). Other users are not blocked.
    SQLite ignores FOR UPDATE; it is only used for local dev/tests, where this race is acceptable.
    """
    db.execute(select(User.id).where(User.id == user_id).with_for_update())
