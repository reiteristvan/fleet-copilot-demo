"""Fixtures for the tests that need a live database.

The database tests skip rather than fail when PostgreSQL is not reachable. CI
runs no PostgreSQL service, and `just test` is meant to be runnable on a laptop
without `just up` first -- a suite that fails for want of a container teaches
people to ignore it. The skip message names the command that would fix it, so a
skipped run is informative rather than silent.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from fleet_copilot.config import Settings, load_settings
from fleet_copilot.db import with_credentials

SKIP_REASON = "no database reachable; run `just up` and `just db-reset` to exercise these"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Application settings, or skip if configuration is missing."""
    try:
        return load_settings()
    except Exception:  # noqa: BLE001 - any failure here means "cannot test"
        pytest.skip(SKIP_REASON)


@pytest.fixture(scope="session")
def database_url(settings: Settings) -> str:
    """A URL that connects, or skip."""
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('fleet.machine')")
                if cursor.fetchone()[0] is None:  # type: ignore[index]
                    pytest.skip("fleet schema not migrated; run `just db-migrate`")
    except psycopg.Error:
        pytest.skip(SKIP_REASON)
    return settings.database_url


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection as the owning role, rolled back after each test."""
    with psycopg.connect(database_url) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def readonly_connection(database_url: str, settings: Settings) -> Iterator[psycopg.Connection]:
    """A connection as copilot_ro, the role the agent uses."""
    url = with_credentials(
        database_url, user=settings.copilot_ro_user, password=settings.copilot_ro_password
    )
    try:
        with psycopg.connect(url) as conn:
            yield conn
            conn.rollback()
    except psycopg.OperationalError:
        pytest.skip("copilot_ro cannot log in; run `just db-migrate`")


@pytest.fixture
def seeded(connection: psycopg.Connection) -> psycopg.Connection:
    """Skip unless the fleet has actually been seeded."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM fleet.telemetry_sample")
        row = cursor.fetchone()
    if row is None or row[0] == 0:
        pytest.skip("fleet not seeded; run `just db-seed`")
    return connection
