"""Alembic environment: runs migrations against DATABASE_URL using our SQLAlchemy models.

Usage (from backend/):
    alembic upgrade head                                   # apply all migrations
    alembic revision --autogenerate -m "describe change"   # after editing src/db/models.py

The database URL comes from src.config (environment / backend/.env), never from alembic.ini.
Tests may pass a different URL with config.set_main_option("sqlalchemy.url", ...).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.config import DATABASE_URL
from src.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    # Escape % so configparser doesn't treat URL-encoded passwords as interpolation
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return (config.get_main_option("sqlalchemy.url") or "").startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting (alembic upgrade head --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=_is_sqlite(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite can't ALTER most things; batch mode recreates the table instead
            render_as_batch=_is_sqlite(),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
