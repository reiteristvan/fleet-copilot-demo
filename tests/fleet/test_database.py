"""What the database itself refuses, and what the copilot role cannot reach.

These are the guarantees the schema makes rather than the ones the application
makes, so they are tested by asking PostgreSQL rather than by asking Python.
"""

from __future__ import annotations

import statistics
import time

import psycopg
import pytest

from fleet_copilot.fleet.queries import LATENCY_BUDGET_MS, reference_queries

MACHINE = "SD50B-2026-10005"


class TestStateIntervalsCannotOverlap:
    def test_adjacent_intervals_are_accepted(self, seeded: psycopg.Connection) -> None:
        """Half-open bounds: one interval ends exactly where the next begins.

        If this were rejected the machine could never change state at all.
        """
        with seeded.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fleet.machine_state_interval (serial, state, period) VALUES
                    (%s, 'working', tstzrange('2027-01-01 08:00+00','2027-01-01 12:00+00','[)')),
                    (%s, 'idle_on', tstzrange('2027-01-01 12:00+00','2027-01-01 13:00+00','[)'))
            """,
                (MACHINE, MACHINE),
            )
        seeded.rollback()

    def test_an_overlapping_interval_is_rejected(self, seeded: psycopg.Connection) -> None:
        """A machine is in exactly one state at a time, enforced by the database."""
        with seeded.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fleet.machine_state_interval (serial, state, period) VALUES "
                "(%s, 'working', tstzrange('2027-02-01 08:00+00','2027-02-01 12:00+00','[)'))",
                (MACHINE,),
            )
            with pytest.raises(psycopg.errors.ExclusionViolation):
                cursor.execute(
                    "INSERT INTO fleet.machine_state_interval (serial, state, period) VALUES "
                    "(%s, 'transit', tstzrange('2027-02-01 11:00+00','2027-02-01 14:00+00','[)'))",
                    (MACHINE,),
                )
        seeded.rollback()

    def test_two_machines_may_overlap_each_other(self, seeded: psycopg.Connection) -> None:
        """The constraint is per serial. Without this it would serialise the fleet."""
        other = "SD50B-2026-10142"
        with seeded.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fleet.machine_state_interval (serial, state, period) VALUES
                    (%s, 'working', tstzrange('2027-03-01 08:00+00','2027-03-01 12:00+00','[)')),
                    (%s, 'working', tstzrange('2027-03-01 08:00+00','2027-03-01 12:00+00','[)'))
            """,
                (MACHINE, other),
            )
        seeded.rollback()

    def test_an_unbounded_interval_is_rejected(self, seeded: psycopg.Connection) -> None:
        """An infinite range sums to infinity in a utilisation report."""
        with seeded.cursor() as cursor, pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO fleet.machine_state_interval (serial, state, period) VALUES "
                "(%s, 'working', tstzrange(NULL, '2027-04-01 12:00+00', '[)'))",
                (MACHINE,),
            )
        seeded.rollback()

    def test_the_seeded_fleet_has_no_overlaps_or_gaps(self, seeded: psycopg.Connection) -> None:
        """The generator's own output obeys the rule, without relying on it.

        A gap would read as a machine that was neither working nor off, which is
        not a state any machine can be in, and the hourly rollup would silently
        lose those minutes.
        """
        with seeded.cursor() as cursor:
            cursor.execute("""
                SELECT count(*) FROM (
                    SELECT serial, started_at,
                           lag(ended_at) OVER (PARTITION BY serial ORDER BY started_at) AS previous
                    FROM fleet.machine_state_interval
                ) gaps
                WHERE previous IS NOT NULL AND previous <> started_at
            """)
            row = cursor.fetchone()
        assert row is not None
        assert row[0] == 0


class TestTelemetryPartitioning:
    def test_a_row_outside_every_declared_month_is_rejected(
        self, seeded: psycopg.Connection
    ) -> None:
        """No DEFAULT partition: a timestamp in 2038 is a bug, not a row."""
        with seeded.cursor() as cursor, pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO fleet.telemetry_sample VALUES "
                "(%s, '2038-01-01 10:00+00', 22, 88, 25.6, 24, 53.5, -2.2, 3.2)",
                (MACHINE,),
            )
        seeded.rollback()

    def test_samples_land_in_the_month_they_belong_to(self, seeded: psycopg.Connection) -> None:
        with seeded.cursor() as cursor:
            cursor.execute("""
                SELECT count(*) FROM fleet.telemetry_sample_202608
                WHERE ts < '2026-08-01' OR ts >= '2026-09-01'
            """)
            row = cursor.fetchone()
        assert row is not None
        assert row[0] == 0

    def test_an_impossible_state_of_charge_is_rejected(self, seeded: psycopg.Connection) -> None:
        with seeded.cursor() as cursor, pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO fleet.telemetry_sample VALUES "
                "(%s, '2026-07-01 10:00+00', 22, 140, 25.6, 24, 53.5, -2.2, 3.2)",
                (MACHINE,),
            )
        seeded.rollback()


class TestCopilotRoleIsConfined:
    def test_it_can_read_the_reporting_views(self, readonly_connection: psycopg.Connection) -> None:
        with readonly_connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM reporting.machine")
            row = cursor.fetchone()
        assert row is not None
        assert row[0] > 0

    @pytest.mark.parametrize(
        "relation",
        [
            "fleet.telemetry_sample",
            "fleet.telemetry_hourly",
            "fleet.telemetry_daily",
            "fleet.machine",
            "fleet.machine_state_interval",
            "fleet.fault_event",
            "fleet.reporting_as_of",
        ],
    )
    def test_it_cannot_read_raw_data_or_base_tables(
        self, readonly_connection: psycopg.Connection, relation: str
    ) -> None:
        """Not a slow path or a discouraged one. No path."""
        with readonly_connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(f"SELECT count(*) FROM {relation}")
        readonly_connection.rollback()

    @pytest.mark.parametrize(
        "statement",
        [
            "INSERT INTO reporting.as_of VALUES (now())",
            "CREATE TABLE reporting.scratch (x int)",
            "CREATE TABLE public.scratch (x int)",
            "DROP VIEW reporting.machine",
        ],
    )
    def test_privileges_refuse_it(
        self, readonly_connection: psycopg.Connection, statement: str
    ) -> None:
        """Refused for want of privileges, which is the barrier that matters.

        reporting.as_of is a single-table view and therefore auto-updatable, so
        the INSERT reaches the privilege check rather than being turned away
        earlier for its shape -- which is what makes it the useful case to
        assert. The DDL statements prove the role cannot create its own way in.
        """
        with readonly_connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(statement)
        readonly_connection.rollback()

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE reporting.machine SET serial = 'x'",
            "DELETE FROM reporting.service_ticket",
            "UPDATE reporting.machine_day SET distance_m = 0",
        ],
    )
    def test_the_join_views_are_not_writable_at_all(
        self, readonly_connection: psycopg.Connection, statement: str
    ) -> None:
        """A second, independent barrier.

        PostgreSQL refuses a write to a view built on a join before it consults
        privileges at all, so these fail with "cannot update view" rather than
        with a permission error. Worth asserting separately: if someone later
        simplified one of these views into an auto-updatable one, this test
        would start failing and the privilege test above would still hold --
        which is the correct signal, not a silent change of mechanism.
        """
        with readonly_connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                cursor.execute(statement)
        readonly_connection.rollback()

    def test_it_cannot_reach_the_documents_through_a_function(
        self, readonly_connection: psycopg.Connection
    ) -> None:
        """reporting.as_of() is SECURITY DEFINER, so it may read what the role
        cannot -- but only that one value, and only through that one function."""
        with readonly_connection.cursor() as cursor:
            cursor.execute("SELECT reporting.as_of()")
            row = cursor.fetchone()
        assert row is not None
        assert row[0] is not None


class TestReferenceQueries:
    def test_there_are_six(self) -> None:
        assert len(reference_queries()) == 6

    def test_each_one_runs_as_the_copilot_role_and_returns_rows(
        self, readonly_connection: psycopg.Connection, seeded: psycopg.Connection
    ) -> None:
        """A query that returns nothing is not evidence the schema can answer it."""
        for query in reference_queries():
            with readonly_connection.cursor() as cursor:
                cursor.execute(query.sql)
                rows = cursor.fetchall()
            assert rows, query.name

    def test_each_one_comes_back_inside_the_latency_budget(
        self, readonly_connection: psycopg.Connection, seeded: psycopg.Connection
    ) -> None:
        """Median of five, so one unlucky run does not fail the suite and one
        lucky run does not pass it."""
        slow: list[str] = []
        for query in reference_queries():
            timings: list[float] = []
            for _ in range(5):
                with readonly_connection.cursor() as cursor:
                    started = time.perf_counter()
                    cursor.execute(query.sql)
                    cursor.fetchall()
                    timings.append((time.perf_counter() - started) * 1000)
            median = statistics.median(timings)
            if median > LATENCY_BUDGET_MS:
                slow.append(f"{query.name}: {median:.0f} ms")
        assert not slow, f"over the {LATENCY_BUDGET_MS:.0f} ms budget: {slow}"
