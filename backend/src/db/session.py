"""Database engine and session setup.

DATABASE_URL decides the backend: SQLite locally, Postgres when deployed.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.config import DATABASE_URL
from src.db.models import Base


def make_engine(url: str) -> Engine:
    """Create an engine. For SQLite, also turn on foreign-key enforcement."""
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)

    # check_same_thread=False: FastAPI may use a session from a different thread
    engine = create_engine(url, connect_args={"check_same_thread": False})

    # SQLite ignores foreign keys unless this pragma is set on every connection.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine: Engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(target_engine: Engine | None = None) -> None:
    """Create all tables if they don't exist (migrations come in Stage 8)."""
    Base.metadata.create_all(target_engine or engine)


def get_db() -> Iterator[Session]:
    """Yield a session and always close it. Used as a FastAPI dependency in Stage 6."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
