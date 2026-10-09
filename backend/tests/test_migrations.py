"""Alembic migration tests.

- The migrations must produce exactly the schema the models describe (no drift).
- upgrade -> downgrade -> upgrade must work.
Runs on a temporary SQLite file by default; the `postgres` tests run the same checks on a
real Postgres when TEST_POSTGRES_URL is set (see README: docker run postgres).
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from src.config import BACKEND_DIR, normalize_database_url
from src.db.models import Base

EXPECTED_TABLES = {"users", "research_runs", "run_events", "alembic_version"}


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logging"] = False  # keep pytest's log capture intact
    return config


def schema_diff(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            return compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()


def table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def check_migrations(url: str) -> None:
    config = alembic_config(url)
    command.upgrade(config, "head")
    assert table_names(url) == EXPECTED_TABLES
    assert schema_diff(url) == [], "models and migrations have drifted: add a migration"

    command.downgrade(config, "base")
    assert table_names(url) == {"alembic_version"}

    command.upgrade(config, "head")
    assert table_names(url) == EXPECTED_TABLES


def test_migrations_on_sqlite(tmp_path: Path) -> None:
    check_migrations(f"sqlite:///{tmp_path / 'migrations.db'}")


@pytest.mark.parametrize(("raw", "expected"), [
    ("postgres://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
    ("postgresql://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
    ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ("sqlite:///./app.db", "sqlite:///./app.db"),
])
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


# ---------- Real Postgres (opt-in: TEST_POSTGRES_URL=... pytest -m postgres) ----------

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="set TEST_POSTGRES_URL to run against Postgres")
def test_migrations_on_postgres() -> None:
    url = normalize_database_url(POSTGRES_URL or "")
    command.downgrade(alembic_config(url), "base")  # start clean even if a previous run failed
    check_migrations(url)
