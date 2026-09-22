"""Fixtures for the fleet tests.

The connection fixtures these build on moved to `tests/conftest.py` when the
embedding cache became their second consumer. What stays here is the part that
is about the fleet specifically: whether it has been seeded.
"""

from __future__ import annotations

import psycopg
import pytest


@pytest.fixture
def seeded(connection: psycopg.Connection) -> psycopg.Connection:
    """Skip unless the fleet has actually been seeded."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM fleet.telemetry_sample")
        row = cursor.fetchone()
    if row is None or row[0] == 0:
        pytest.skip("fleet not seeded; run `just db-seed`")
    return connection
