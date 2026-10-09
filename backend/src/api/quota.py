"""Per-user quota, checked BEFORE any LLM or graph work (each run costs real tokens).

Two limits, both counted from research_runs (no extra table):
- at most MAX_ACTIVE_RUNS_PER_USER runs pending/running at once;
- at most RUNS_PER_DAY runs started in the last 24 hours (rolling window).
Failed runs still count toward the daily limit: they consumed LLM calls too.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.config import MAX_ACTIVE_RUNS_PER_USER, RUNS_PER_DAY
from src.db.models import count_active_runs, count_runs_since

QUOTA_WINDOW = timedelta(hours=24)


def check_quota(
    db: Session,
    user_id: str,
    now: datetime | None = None,
    runs_per_day: int = RUNS_PER_DAY,
    max_active: int = MAX_ACTIVE_RUNS_PER_USER,
) -> None:
    """Raise HTTP 429 if this user may not start another run right now."""
    now = now or datetime.now(timezone.utc)

    if count_active_runs(db, user_id) >= max_active:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You already have {max_active} research run(s) in progress. Wait for it to finish.",
        )

    used = count_runs_since(db, user_id, now - QUOTA_WINDOW)
    if used >= runs_per_day:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily limit reached: {used}/{runs_per_day} research runs in the last 24 hours.",
        )
