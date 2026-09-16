"""Expose the reporting clock as a STABLE function, not only as a view.

`CROSS JOIN reporting.as_of` reads well and plans badly. The planner cannot know
what a one-row table contains, so `day > (a.as_of - interval '7 days')` becomes a
join filter applied *after* the rows have been produced: on the reference
queries it materialised all 3 640 daily rows, discarded 3 360 of them, and took
330 ms to return 40.

A STABLE function is evaluated once per query and can therefore be used as an
index qualifier, which turns the same predicate into an index scan over the week
that was asked for.

Revision ID: 0006_as_of_function
Revises: 0005_reporting
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_as_of_function"
down_revision: str | None = "0005_reporting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READONLY_ROLE = "copilot_ro"


def upgrade() -> None:
    # STABLE, not IMMUTABLE: it reads a table, and the contents can change
    # between statements. STABLE is exactly the promise the planner needs --
    # constant within one statement -- and claiming IMMUTABLE would let the
    # value be folded into a cached plan and go stale after the next seed.
    # SECURITY DEFINER because, unlike a view, a function runs with the
    # *invoker's* privileges by default -- so copilot_ro, which cannot read the
    # fleet schema, would be refused inside the function body. search_path is
    # pinned in the same breath: a SECURITY DEFINER function that inherits the
    # caller's search_path can be pointed at objects the caller controls.
    op.execute("""
        CREATE FUNCTION reporting.as_of() RETURNS timestamptz
        LANGUAGE sql STABLE PARALLEL SAFE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT as_of FROM fleet.reporting_as_of
        $fn$
    """)
    op.execute(
        "COMMENT ON FUNCTION reporting.as_of() IS "
        "'The instant reporting treats as now. Prefer this to the as_of view in a "
        "WHERE clause: as a STABLE function it can be used as an index qualifier, "
        "where joining the view cannot and costs a full scan of the rollup.'"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION reporting.as_of() TO {READONLY_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS reporting.as_of()")
