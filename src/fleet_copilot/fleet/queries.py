"""The reference queries the copilot is expected to be able to answer.

Held as ``.sql`` files rather than as strings in Python, because they are the
specification of what the reporting schema is for. A question that cannot be
answered from these views is a gap in the schema, and keeping the queries where
they can be run by hand — against the read-only role, exactly as the agent will
— is what makes that checkable rather than assumed.

Every one of them reads ``reporting.as_of`` instead of ``now()``. The fleet
window ends at a fixed instant, so ``now()`` asks about an empty week.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

QUERY_DIR = Path(__file__).resolve().parent / "queries"

LATENCY_BUDGET_MS = 500.0
"""What each reference query must come back within.

Not a performance target picked for comfort: it is the point at which an
interactive answer stops feeling interactive, and it is the reason the agent
reads rollups rather than samples.
"""


@dataclass(frozen=True, slots=True)
class ReferenceQuery:
    """One named query, and the SQL it runs."""

    name: str
    sql: str

    @property
    def title(self) -> str:
        """A human-readable name, from the filename."""
        return self.name.split("_", 1)[1].replace("_", " ").capitalize()


@cache
def reference_queries() -> tuple[ReferenceQuery, ...]:
    """Return every reference query, in file order.

    Ordered by the numeric prefix so the set reads as a tour of the schema
    rather than as an alphabetical accident.
    """
    if not QUERY_DIR.is_dir():
        msg = f"no query directory at {QUERY_DIR}"
        raise FileNotFoundError(msg)
    queries = tuple(
        ReferenceQuery(name=path.stem, sql=path.read_text(encoding="utf-8"))
        for path in sorted(QUERY_DIR.glob("*.sql"))
    )
    if not queries:
        msg = f"no .sql files in {QUERY_DIR}"
        raise FileNotFoundError(msg)
    return queries


def query_by_name(name: str) -> ReferenceQuery:
    """Return the reference query called ``name``."""
    for query in reference_queries():
        if query.name == name:
            return query
    available = [query.name for query in reference_queries()]
    msg = f"no reference query named {name!r}; available: {available}"
    raise KeyError(msg)
