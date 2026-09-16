"""Alembic environment.

The database URL comes from the application's own :class:`Settings` rather than
from ``alembic.ini``. A URL in two places is a URL that eventually disagrees
with itself, and the failure — migrations applied to one database while the API
reads another — looks like missing data rather than like a configuration
mistake.

``target_metadata`` is ``None`` on purpose. Every migration in this project is
hand-written SQL (ADR 0004): native partitioning, an exclusion constraint over a
``tstzrange``, materialised views, a role and its grants. Autogenerate cannot
express any of them and would propose dropping all of them on every run.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from fleet_copilot.config import load_settings
from fleet_copilot.db import sqlalchemy_url

target_metadata = None


def database_url() -> str:
    """Return the SQLAlchemy URL for the configured database."""
    return sqlalchemy_url(load_settings().database_url)


def run_migrations_offline() -> None:
    """Emit the migrations as SQL, without connecting."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply the migrations against a live connection."""
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
