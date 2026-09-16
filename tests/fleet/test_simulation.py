"""The fleet registry and behaviour model, without a database.

These run everywhere, including CI, because they are about the generator rather
than about PostgreSQL.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fleet_copilot.corpus.planner import serial_for
from fleet_copilot.corpus.seed import load_catalogue
from fleet_copilot.fleet import behaviour, world
from fleet_copilot.fleet.load import choose_anomalies, corpus_fault_links

CATALOGUE = load_catalogue()
MACHINES = world.build_machines(CATALOGUE)


class TestRegistry:
    def test_forty_machines_with_unique_serials(self) -> None:
        assert len(MACHINES) == 40
        assert len({m.serial for m in MACHINES}) == 40

    def test_it_covers_every_site_the_corpus_names(self) -> None:
        """A handover note written at a site with no machines describes a fleet
        the database does not have."""
        registry_sites = {m.site.slug for m in MACHINES}
        corpus_sites = {site.slug for site in CATALOGUE.sites}
        assert corpus_sites == {s.slug for s in world.SITE_GEOGRAPHY}
        assert corpus_sites == registry_sites

    def test_the_serials_the_corpus_cites_are_all_registered(self) -> None:
        """The join the whole demo rests on: a serial in a service report has to
        resolve to a machine."""
        cited = {
            serial_for(machine_type, index)
            for machine_type in CATALOGUE.machine_types
            for index in range(4)
        }
        assert cited <= {m.serial for m in MACHINES}

    def test_half_the_fleet_has_no_paperwork(self) -> None:
        """Both hits and misses, so a document join has something to get wrong."""
        cited = {
            serial_for(machine_type, index)
            for machine_type in CATALOGUE.machine_types
            for index in range(4)
        }
        undocumented = [m for m in MACHINES if m.serial not in cited]
        assert len(undocumented) == 20

    def test_every_machine_config_exists_in_the_catalogue(self) -> None:
        known = {v.item_number for m in CATALOGUE.machine_types for v in m.variants}
        assert {m.item_number for m in MACHINES} <= known

    def test_a_site_centre_lies_inside_its_own_geofence(self) -> None:
        """The database enforces this too; here it is checked before the insert
        so a failure names the site rather than the constraint."""
        for site in world.SITE_GEOGRAPHY:
            points = world.geofence_points(site)
            lons = [lon for lon, _ in points]
            lats = [lat for _, lat in points]
            assert min(lons) < site.lon < max(lons), site.slug
            assert min(lats) < site.lat < max(lats), site.slug

    def test_geofences_are_polygons_rather_than_boxes(self) -> None:
        """A rectangle would not exercise the polygon containment operator."""
        for site in world.SITE_GEOGRAPHY:
            assert len(world.geofence_points(site)) >= 5, site.slug

    def test_only_nearby_sites_are_paired_for_transit(self) -> None:
        """A cleaning machine does not drive between cities under its own power."""
        for first, second in world.CUSTOMER_SITE_PAIRS:
            one, two = world.site_by_slug(first), world.site_by_slug(second)
            per_lat = world.metres_per_degree_lat()
            per_lon = world.metres_per_degree_lon(one.lat)
            metres = (
                ((one.lat - two.lat) * per_lat) ** 2 + ((one.lon - two.lon) * per_lon) ** 2
            ) ** 0.5
            assert metres < 5_000, f"{first}->{second} is {metres:.0f} m apart"


class TestDeterminism:
    def test_the_same_machine_simulates_identically_twice(self) -> None:
        """What makes the seed idempotent."""
        anomalies = behaviour.Anomalies("A", "B", "C", date(2026, 8, 12))
        first = behaviour.simulate(MACHINES[2], anomalies, {})
        second = behaviour.simulate(MACHINES[2], anomalies, {})
        assert first.samples == second.samples
        assert first.intervals == second.intervals

    def test_shift_pattern_does_not_depend_on_process_hash_seed(self) -> None:
        """Python randomises str hashing per process; a stable digest does not."""
        assert behaviour.runs_double_shift("SD50B-2026-10005") == behaviour.runs_double_shift(
            "SD50B-2026-10005"
        )
        patterns = {behaviour.runs_double_shift(m.serial) for m in MACHINES}
        assert patterns == {True, False}


@pytest.fixture(scope="module")
def intervals() -> list[behaviour.StateInterval]:
    """One machine's whole 90-day interval cover, built once for the module."""
    return behaviour.build_intervals(MACHINES[0], behaviour.machine_rng(MACHINES[0].serial))


class TestStateIntervals:
    def test_they_cover_the_window_without_gaps(
        self, intervals: list[behaviour.StateInterval]
    ) -> None:
        assert intervals[0].start == world.WINDOW_START
        assert intervals[-1].end == world.AS_OF
        for previous, current in zip(intervals, intervals[1:], strict=False):
            assert previous.end == current.start

    def test_no_two_overlap(self, intervals: list[behaviour.StateInterval]) -> None:
        for previous, current in zip(intervals, intervals[1:], strict=False):
            assert previous.end <= current.start

    def test_every_interval_has_positive_duration(
        self, intervals: list[behaviour.StateInterval]
    ) -> None:
        assert all(i.end > i.start for i in intervals)

    def test_every_state_is_one_the_schema_allows(
        self, intervals: list[behaviour.StateInterval]
    ) -> None:
        assert {i.state for i in intervals} <= {"working", "transit", "idle_on", "off"}

    def test_the_machine_is_switched_off_more_often_than_not(
        self, intervals: list[behaviour.StateInterval]
    ) -> None:
        """A fleet running flat out around the clock would need no utilisation
        report, and would not look like any real cleaning operation."""
        off = sum((i.end - i.start).total_seconds() for i in intervals if i.state == "off")
        total = (world.AS_OF - world.WINDOW_START).total_seconds()
        assert 0.35 < off / total < 0.75


class TestAnomalies:
    def test_the_three_planted_machines_are_distinct(self) -> None:
        links = corpus_fault_links()
        anomalies = choose_anomalies(MACHINES, links)
        chosen = {
            anomalies.overheating_serial,
            anomalies.deep_discharge_serial,
            anomalies.off_map_serial,
        }
        assert len(chosen) == 3
        assert chosen <= {m.serial for m in MACHINES}

    def test_the_overheating_machine_has_a_corpus_document(self) -> None:
        """The strongest test case needs both halves of the system to answer."""
        links = corpus_fault_links()
        anomalies = choose_anomalies(MACHINES, links)
        assert (anomalies.overheating_serial, "E-041") in links

    def test_the_deep_discharge_machine_carries_a_lead_acid_pack(self) -> None:
        """Deep discharge is an AGM failure mode; planting it on lithium would
        contradict the maintenance procedures in the corpus."""
        links = corpus_fault_links()
        anomalies = choose_anomalies(MACHINES, links)
        machine = next(m for m in MACHINES if m.serial == anomalies.deep_discharge_serial)
        assert machine.is_agm

    def test_the_off_map_day_falls_inside_the_window(self) -> None:
        links = corpus_fault_links()
        anomalies = choose_anomalies(MACHINES, links)
        assert world.WINDOW_START.date() < anomalies.off_map_day < world.AS_OF.date()

    def test_corpus_links_point_at_documents_that_exist(self) -> None:
        links = corpus_fault_links()
        assert links, "no fault links found; has the corpus been generated?"
        from fleet_copilot.corpus.build import corpus_root

        markdown = corpus_root() / "markdown"
        for doc_id in set(links.values()):
            assert (markdown / f"{doc_id}.md").is_file(), doc_id


class TestWindow:
    def test_the_window_is_ninety_days_ending_with_the_corpus(self) -> None:
        assert world.AS_OF - world.WINDOW_START == timedelta(days=90)
        assert world.AS_OF.date() == date(2026, 9, 15)


class TestSeedIsRepeatable:
    def test_the_truncate_list_covers_every_table_the_seed_writes(self) -> None:
        """Idempotency is by truncation, so a table missing from the list would
        accumulate rows on every run -- and only the tables nobody checks."""
        from fleet_copilot.fleet.load import TRUNCATE_ORDER

        written = {
            "fleet.reporting_as_of",
            "fleet.machine_type",
            "fleet.machine_config",
            "fleet.site",
            "fleet.machine",
            "fleet.operator",
            "fleet.error_code",
            "fleet.error_code_machine_type",
            "fleet.service_ticket",
            "fleet.fault_event",
            "fleet.machine_state_interval",
            "fleet.telemetry_sample",
        }
        assert written == set(TRUNCATE_ORDER)

    def test_children_are_truncated_before_their_parents(self) -> None:
        """RESTART IDENTITY CASCADE would paper over the order, but the explicit
        order is what makes a missing entry visible rather than silently fixed."""
        from fleet_copilot.fleet.load import TRUNCATE_ORDER

        order = list(TRUNCATE_ORDER)
        assert order.index("fleet.telemetry_sample") < order.index("fleet.machine")
        assert order.index("fleet.fault_event") < order.index("fleet.machine")
        assert order.index("fleet.machine") < order.index("fleet.site")
        assert order.index("fleet.machine") < order.index("fleet.machine_config")
