"""Run the reference queries as the copilot role and report timings.

A command rather than a test because the useful form of this question is "how
does the reporting schema behave right now", which a developer asks while
changing a view. The test asserts the budget; this shows the numbers.
"""

from __future__ import annotations

import statistics
import time

import psycopg

from fleet_copilot.config import load_settings
from fleet_copilot.db import with_credentials
from fleet_copilot.fleet.queries import LATENCY_BUDGET_MS, reference_queries

RUNS = 5


def main() -> int:
    """Time every reference query, returning non-zero if any misses the budget."""
    settings = load_settings()
    url = with_credentials(
        settings.database_url,
        user=settings.copilot_ro_user,
        password=settings.copilot_ro_password,
    )
    over_budget: list[str] = []
    with psycopg.connect(url) as connection:
        for query in reference_queries():
            timings: list[float] = []
            rows = 0
            for _ in range(RUNS):
                with connection.cursor() as cursor:
                    started = time.perf_counter()
                    cursor.execute(query.sql)
                    rows = len(cursor.fetchall())
                    timings.append((time.perf_counter() - started) * 1000)
            median = statistics.median(timings)
            flag = "ok  " if median <= LATENCY_BUDGET_MS else "SLOW"
            print(f"{flag} {median:8.1f} ms  {rows:5d} rows  {query.name}")
            if median > LATENCY_BUDGET_MS:
                over_budget.append(query.name)

    print(f"budget {LATENCY_BUDGET_MS:.0f} ms, median of {RUNS} runs, as copilot_ro")
    return 1 if over_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
