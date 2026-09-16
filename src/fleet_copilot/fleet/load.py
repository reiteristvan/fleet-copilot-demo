"""Generate the fleet and load it into PostgreSQL, repeatably.

Idempotent by truncation rather than by upsert. The generator is deterministic,
so a second run produces the same rows as the first; reconciling them row by row
would be slower, more code, and would still end at the same place. Everything
happens in one transaction, so a failed seed leaves the previous fleet intact
rather than half of a new one.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import psycopg

from fleet_copilot.corpus.build import corpus_root
from fleet_copilot.corpus.models import Catalogue
from fleet_copilot.fleet import behaviour
from fleet_copilot.fleet.behaviour import Anomalies, FaultEvent, StateInterval
from fleet_copilot.fleet.world import (
    AS_OF,
    OPERATOR_NAMES,
    SEED,
    SITE_GEOGRAPHY,
    WINDOW_START,
    FleetMachine,
    build_machines,
    geofence_points,
    point_literal,
    polygon_literal,
)

FRONT_MATTER_SERIALS = re.compile(r"^serials:\s*\[(.*?)\]", re.MULTILINE)
FRONT_MATTER_TYPE = re.compile(r"^type:\s*(\S+)", re.MULTILINE)
ERROR_CODE = re.compile(r"\bE-\d{3}\b")
SERIAL_LITERAL = re.compile(r"[A-Z0-9]{4,10}-\d{4}-\d{5}")

Row = tuple[object, ...]
"""One row on its way into COPY. Heterogeneous by nature, so typed loosely."""

OFF_MAP_DAY: Final = date(2026, 8, 12)
"""The day one machine reports from nowhere any site covers."""

TRUNCATE_ORDER: Final = (
    "fleet.telemetry_sample",
    "fleet.machine_state_interval",
    "fleet.fault_event",
    "fleet.service_ticket",
    "fleet.operator",
    "fleet.machine",
    "fleet.site",
    "fleet.machine_config",
    "fleet.machine_type",
    "fleet.error_code_machine_type",
    "fleet.error_code",
    "fleet.reporting_as_of",
)


@dataclass(frozen=True, slots=True)
class Ticket:
    """A service ticket raised against a machine."""

    ticket_id: str
    serial: str
    opened_at: object
    closed_at: object | None
    status: str
    summary: str
    operator_id: int


@dataclass(frozen=True, slots=True)
class LoadReport:
    """What a seed run wrote."""

    machines: int
    sites: int
    intervals: int
    samples: int
    faults: int
    tickets: int
    linked_faults: int

    def describe(self) -> str:
        """One ASCII block, safe for a cp1252 console."""
        return (
            f"{self.machines} machines across {self.sites} sites\n"
            f"  state intervals : {self.intervals:,}\n"
            f"  telemetry rows  : {self.samples:,}\n"
            f"  fault events    : {self.faults:,} "
            f"({self.linked_faults} linked to a corpus document)\n"
            f"  service tickets : {self.tickets:,}"
        )


def corpus_fault_links(root: Path | None = None) -> dict[tuple[str, str], str]:
    """Map (serial, error code) to the corpus document that describes it.

    Read out of the generated Markdown rather than declared here, so the link is
    a fact about what the documents actually say. Every document has a Markdown
    copy even when it is published as a PDF, which is what makes this possible
    without parsing PDFs.
    """
    resolved = (root or corpus_root()) / "markdown"
    links: dict[tuple[str, str], str] = {}
    if not resolved.is_dir():
        return links

    for path in sorted(resolved.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        kind = FRONT_MATTER_TYPE.search(text)
        if kind is None or kind.group(1) not in {"service_report", "fault_report"}:
            continue
        serials_match = FRONT_MATTER_SERIALS.search(text)
        if serials_match is None:
            continue
        serials = SERIAL_LITERAL.findall(serials_match.group(1))
        codes = set(ERROR_CODE.findall(text))
        for serial in serials:
            for code in codes:
                links.setdefault((serial, code), path.stem)
    return links


def choose_anomalies(
    machines: Sequence[FleetMachine], links: dict[tuple[str, str], str]
) -> Anomalies:
    """Pick the three machines that misbehave, preferring ones with paperwork.

    A machine whose fault the corpus already describes makes the strongest test
    case: the answer requires both halves of the system, and an agent that reads
    only one of them gets a visibly incomplete answer rather than a wrong one.
    """
    documented = {serial for serial, _ in links}

    overheating = next(
        (m.serial for m in machines if (m.serial, "E-041") in links),
        next(m.serial for m in machines if m.machine_type.code == "SD-50B"),
    )
    deep_discharge = next(
        (
            m.serial
            for m in machines
            if m.is_agm and m.serial in documented and m.serial != overheating
        ),
        next(m.serial for m in machines if m.is_agm and m.serial != overheating),
    )
    off_map = next(m.serial for m in machines if m.serial not in {overheating, deep_discharge})
    return Anomalies(overheating, deep_discharge, off_map, OFF_MAP_DAY)


def build_tickets(
    faults: Sequence[FaultEvent], operator_ids: dict[str, int], machines: Sequence[FleetMachine]
) -> list[Ticket]:
    """Raise tickets from a sample of faults, leaving some still open.

    Not every fault becomes a ticket -- a code that clears itself usually does
    not -- and not every ticket is closed, or "open tickets by site" would have
    nothing to report.
    """
    rng = random.Random(f"{SEED}:tickets")
    site_of = {m.serial: m.site.slug for m in machines}
    operators_by_site: dict[str, list[int]] = {}
    for name, identifier in operator_ids.items():
        site = name.split("@", 1)[1]
        operators_by_site.setdefault(site, []).append(identifier)

    tickets: list[Ticket] = []
    for fault in sorted(faults, key=lambda f: (f.occurred_at, f.serial, f.code)):
        if rng.random() > 0.08:
            continue
        index = len(tickets) + 1
        site = site_of[fault.serial]
        opened = fault.occurred_at + timedelta(minutes=rng.randrange(10, 240))
        if opened >= AS_OF:
            continue

        # Recent tickets are likelier to still be open, which is what makes the
        # open-tickets report track the last fortnight rather than the whole
        # window.
        days_old = (AS_OF - opened).days
        still_open = rng.random() < (0.75 if days_old < 14 else 0.06)
        if still_open:
            status = "in_progress" if rng.random() < 0.4 else "open"
            closed = None
        else:
            status = "closed"
            closed = opened + timedelta(hours=rng.randrange(4, 96))
            if closed >= AS_OF:
                closed = AS_OF - timedelta(hours=1)

        tickets.append(
            Ticket(
                ticket_id=f"TCK-2026-{index:04d}",
                serial=fault.serial,
                opened_at=opened,
                closed_at=closed,
                status=status,
                summary=f"{fault.code} reported by the machine; investigate.",
                operator_id=rng.choice(operators_by_site[site]),
            )
        )
    return tickets


def _operator_rows(machines: Sequence[FleetMachine]) -> tuple[dict[str, int], list[Row]]:
    """Assign operators to sites, returning their ids and the rows to insert."""
    sites = sorted({m.site.slug for m in machines})
    keys: dict[str, int] = {}
    rows: list[Row] = []
    identifier = 1
    for site in sites:
        for offset in range(3):
            name = OPERATOR_NAMES[(hash_index(site) + offset) % len(OPERATOR_NAMES)]
            key = f"{name}@{site}"
            if key in keys:
                continue
            keys[key] = identifier
            rows.append((identifier, name, site))
            identifier += 1
    return keys, rows


def hash_index(text: str) -> int:
    """A small stable index derived from ``text``."""
    return sum(ord(character) for character in text)


def _copy(cursor: psycopg.Cursor, statement: str, rows: Iterable[Row]) -> int:
    """Stream ``rows`` into the database with COPY, returning the count."""
    written = 0
    with cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            written += 1
    return written


def load_fleet(connection: psycopg.Connection, catalogue: Catalogue) -> LoadReport:
    """Generate the fleet and write it, replacing whatever was there."""
    machines = build_machines(catalogue)
    links = corpus_fault_links()
    anomalies = choose_anomalies(machines, links)

    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {', '.join(TRUNCATE_ORDER)} RESTART IDENTITY CASCADE")

        cursor.execute("INSERT INTO fleet.reporting_as_of (as_of) VALUES (%s)", (AS_OF,))

        _copy(
            cursor,
            "COPY fleet.machine_type (code, family, name_en, name_hu) FROM STDIN",
            [(m.code, m.family.value, m.name_en, m.name_hu) for m in catalogue.machine_types],
        )
        _copy(
            cursor,
            "COPY fleet.machine_config (item_number, machine_type_code, battery_chemistry, "
            "deck, solution_tank_l, recovery_tank_l, hopper_l) FROM STDIN",
            [
                (
                    v.item_number,
                    m.code,
                    v.battery.value,
                    v.deck.value,
                    v.solution_tank_l,
                    v.recovery_tank_l,
                    v.hopper_l,
                )
                for m in catalogue.machine_types
                for v in m.variants
            ],
        )
        _copy(
            cursor,
            "COPY fleet.site (slug, name, address, language, centre, geofence) FROM STDIN",
            [
                (
                    s.slug,
                    s.name,
                    s.address,
                    s.language.value,
                    point_literal(s.lon, s.lat),
                    polygon_literal(geofence_points(s)),
                )
                for s in SITE_GEOGRAPHY
            ],
        )
        _copy(
            cursor,
            "COPY fleet.error_code (code, title_en, title_hu) FROM STDIN",
            [(e.code, e.title_en, e.title_hu) for e in catalogue.error_codes],
        )
        _copy(
            cursor,
            "COPY fleet.error_code_machine_type (code, machine_type_code) FROM STDIN",
            [(e.code, applies) for e in catalogue.error_codes for applies in e.applies_to],
        )
        _copy(
            cursor,
            "COPY fleet.machine (serial, item_number, site_slug, commissioned_on) FROM STDIN",
            [(m.serial, m.item_number, m.site.slug, m.commissioned_on) for m in machines],
        )

        operator_ids, operator_rows = _operator_rows(machines)
        _copy(cursor, "COPY fleet.operator (id, full_name, site_slug) FROM STDIN", operator_rows)
        cursor.execute(
            "SELECT setval(pg_get_serial_sequence('fleet.operator', 'id'), %s)",
            (len(operator_rows),),
        )

        intervals: list[StateInterval] = []
        faults: list[FaultEvent] = []
        samples_written = 0
        for machine in machines:
            run = behaviour.simulate(machine, anomalies, links)
            intervals.extend(run.intervals)
            faults.extend(run.faults)
            samples_written += _copy(
                cursor,
                "COPY fleet.telemetry_sample (serial, ts, internal_temp_c, battery_soc_pct, "
                "battery_voltage_v, battery_temp_c, lat, lon, speed_kmh) FROM STDIN",
                run.samples,
            )

        _copy(
            cursor,
            "COPY fleet.machine_state_interval (serial, state, period) FROM STDIN",
            [
                (i.serial, i.state, f"[{i.start.isoformat()},{i.end.isoformat()})")
                for i in intervals
            ],
        )

        tickets = build_tickets(faults, operator_ids, machines)
        _copy(
            cursor,
            "COPY fleet.service_ticket (id, serial, opened_at, closed_at, status, summary, "
            "raised_by) FROM STDIN",
            [
                (
                    t.ticket_id,
                    t.serial,
                    t.opened_at,
                    t.closed_at,
                    t.status,
                    t.summary,
                    t.operator_id,
                )
                for t in tickets
            ],
        )
        _copy(
            cursor,
            "COPY fleet.fault_event (serial, code, occurred_at, doc_id) FROM STDIN",
            [(f.serial, f.code, f.occurred_at, f.doc_id) for f in faults],
        )

    return LoadReport(
        machines=len(machines),
        sites=len(SITE_GEOGRAPHY),
        intervals=len(intervals),
        samples=samples_written,
        faults=len(faults),
        tickets=len(tickets),
        linked_faults=sum(1 for f in faults if f.doc_id is not None),
    )


def refresh_rollups(connection: psycopg.Connection) -> None:
    """Rebuild the materialised views. Hourly first: daily is built from it."""
    with connection.cursor() as cursor:
        cursor.execute("REFRESH MATERIALIZED VIEW fleet.telemetry_hourly")
        cursor.execute("REFRESH MATERIALIZED VIEW fleet.telemetry_daily")
        cursor.execute("ANALYZE fleet.telemetry_hourly")
        cursor.execute("ANALYZE fleet.telemetry_daily")


def window() -> tuple[object, object]:
    """The telemetry window, for anything that needs to report it."""
    return WINDOW_START, AS_OF
