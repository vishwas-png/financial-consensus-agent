"""Database engine and session setup.

DATABASE_URL decides the backend: SQLite locally, Postgres when deployed.
The FastAPI dependency that hands out sessions lives in src/api/deps.py.
"""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import DATABASE_URL
from src.db.models import Base


def make_engine(url: str) -> Engine:
    """Create an engine. For SQLite, also turn on foreign-key enforcement."""
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)

    # check_same_thread=False: FastAPI and the run worker use sessions from other threads
    connect_args = {"check_same_thread": False}
    if url in ("sqlite://", "sqlite:///:memory:"):
        # In-memory SQLite exists per connection; StaticPool shares ONE connection so
        # every thread (API requests, background worker) sees the same database. Tests only.
        engine = create_engine(url, connect_args=connect_args, poolclass=StaticPool)
    else:
        engine = create_engine(url, connect_args=connect_args)

    # SQLite ignores foreign keys unless this pragma is set on every connection.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(target_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=target_engine, autoflush=False, expire_on_commit=False)


engine: Engine = make_engine(DATABASE_URL)
SessionLocal = make_session_factory(engine)


def init_db(target_engine: Engine | None = None) -> None:
    """Create all tables if they don't exist (migrations come in Stage 8)."""
    Base.metadata.create_all(target_engine or engine)
