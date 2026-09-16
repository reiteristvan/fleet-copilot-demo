"""Database URLs, shared by the API, the migrations and the fleet seed.

One place that knows how to name the database, because a URL written out in two
places is a URL that eventually disagrees with itself — and the failure that
produces, migrations applied to one database while the application reads
another, looks like missing data rather than like a configuration mistake.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

PSYCOPG_SCHEME = "postgresql+psycopg"
"""SQLAlchemy driver scheme for psycopg 3.

SQLAlchemy selects its driver from the URL scheme, and a bare ``postgresql://``
selects psycopg **2**, which this project does not install. The failure is an
import error at connect time, far from the URL that caused it.
"""


def sqlalchemy_url(database_url: str) -> str:
    """Return ``database_url`` with a scheme SQLAlchemy resolves to psycopg 3."""
    if database_url.startswith("postgresql://"):
        return f"{PSYCOPG_SCHEME}://" + database_url.removeprefix("postgresql://")
    return database_url


def with_credentials(database_url: str, *, user: str, password: str) -> str:
    """Return ``database_url`` pointed at the same database as a different role.

    Used to connect as ``copilot_ro`` from the tests that prove what that role
    cannot do. Building the URL by swapping the credentials keeps the host, port
    and database name in one place: a test that hardcoded its own URL would
    happily pass against a database nobody else was using.
    """
    parts = urlsplit(database_url)
    if not parts.hostname:
        msg = f"cannot swap credentials into a URL with no host: {database_url!r}"
        raise ValueError(msg)

    authority = f"{quote(user, safe='')}:{quote(password, safe='')}@{parts.hostname}"
    if parts.port:
        authority = f"{authority}:{parts.port}"
    return urlunsplit((parts.scheme, authority, parts.path, parts.query, parts.fragment))
