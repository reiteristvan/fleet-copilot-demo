"""The registry: sites, machines, operators and the codes they report.

Everything here is derived from ``data/catalogue.yaml`` — the same file the
document corpus was generated from — plus the geography, which documents do not
carry and the database needs.

The window is fixed rather than relative to the wall clock. The corpus is dated
into 2026, and a telemetry window that slid to "now" would drift away from the
documents a little further every day, until a fault event and the service report
describing it sat months apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from fleet_copilot.corpus.models import Catalogue, Language, MachineType, Variant
from fleet_copilot.corpus.planner import serial_for

AS_OF: Final = datetime(2026, 9, 15, tzinfo=UTC)
"""The instant reporting treats as now.

The end of the corpus date range, so documents and telemetry describe one
timeline. Stored in ``fleet.reporting_as_of`` and read by the reporting views,
so "last 7 days" means the same thing whenever it is asked.
"""

WINDOW_DAYS: Final = 90
WINDOW_START: Final = AS_OF - timedelta(days=WINDOW_DAYS)

MACHINES_PER_TYPE: Final = 8
"""Eight of each of the five types, for forty machines.

The corpus cites the first four serials of each type, so half the fleet has
paperwork and half does not — which is what a real registry looks like, and it
means a join from telemetry to documents has both hits and misses to prove it
handles.
"""

SEED: Final = 20260916
"""Drives every random choice in the simulation. Changing it rewrites the fleet."""

EARTH_RADIUS_M: Final = 6_371_000.0


@dataclass(frozen=True, slots=True)
class SiteGeography:
    """Where a site is, and how big its yard is."""

    slug: str
    name: str
    address: str
    language: Language
    lat: float
    lon: float
    radius_m: float


SITE_GEOGRAPHY: Final = (
    SiteGeography(
        "depot-north",
        "Depot North",
        "Unit 4, Northgate Industrial Park, Manchester M40 2QH",
        Language.EN,
        53.5102,
        -2.2100,
        260.0,
    ),
    SiteGeography(
        "riverside-mall",
        "Riverside Mall",
        "1 Riverside Way, Salford M50 3AG",
        Language.EN,
        53.4740,
        -2.2980,
        220.0,
    ),
    SiteGeography(
        "hangar-seven",
        "Hangar Seven",
        "Hangar 7, Airport Cargo Centre, Manchester M90 5AA",
        Language.EN,
        53.3600,
        -2.2750,
        340.0,
    ),
    SiteGeography(
        "city-hospital",
        "City Hospital",
        "Oxford Road, Manchester M13 9WL",
        Language.EN,
        53.4630,
        -2.2830,
        200.0,
    ),
    SiteGeography(
        "metro-logistics",
        "Metro Logistics Centre",
        "Junction 22 Distribution Park, Oldham OL9 8DQ",
        Language.EN,
        53.5250,
        -2.1950,
        380.0,
    ),
    SiteGeography(
        "harbour-terminal",
        "Harbour Terminal",
        "Berth 12, Port of Liverpool, Bootle L20 1AB",
        Language.EN,
        53.4460,
        -3.0080,
        420.0,
    ),
    SiteGeography(
        "budapest-raktar",
        "Budapest Raktár",
        "Váci út 178, 1138 Budapest",
        Language.HU,
        47.5510,
        19.0720,
        300.0,
    ),
    SiteGeography(
        "debrecen-uzem",
        "Debreceni Üzem",
        "Kishatár út 7, 4031 Debrecen",
        Language.HU,
        47.5200,
        21.6400,
        260.0,
    ),
    SiteGeography(
        "gyor-logisztika",
        "Győri Logisztikai Központ",
        "Ipar út 5, 9027 Győr",
        Language.HU,
        47.6810,
        17.6250,
        280.0,
    ),
)

CUSTOMER_SITE_PAIRS: Final = (
    ("depot-north", "metro-logistics"),
    ("riverside-mall", "city-hospital"),
)
"""Sites close enough that one customer moves machines between them.

Only these pairs generate transit. A machine does not drive sixty kilometres
between cities under its own power, so pairing distant sites would produce
"transit" intervals no cleaning machine could physically perform.
"""

OPERATOR_NAMES: Final = (
    "J. Whitfield",
    "A. Okafor",
    "M. Lindqvist",
    "R. Castellanos",
    "D. Mensah",
    "S. Petrov",
    "T. Nakamura",
    "L. Brennan",
    "K. Szabó",
    "B. Nagy",
    "Z. Kovács",
    "É. Tóth",
    "G. Horváth",
)
"""The same names the corpus handover notes are signed with."""


def metres_per_degree_lon(lat: float) -> float:
    """Metres in one degree of longitude at ``lat``.

    Longitude degrees converge towards the poles, so a geofence built by adding
    a constant to both coordinates is a rectangle on paper and a wedge on the
    ground — noticeably wrong even at Manchester's latitude, where a degree of
    longitude is only 59% of a degree of latitude.
    """
    return (math.pi / 180.0) * EARTH_RADIUS_M * math.cos(math.radians(lat))


def metres_per_degree_lat() -> float:
    """Metres in one degree of latitude."""
    return (math.pi / 180.0) * EARTH_RADIUS_M


def geofence_points(site: SiteGeography, *, vertices: int = 7) -> list[tuple[float, float]]:
    """Return the site boundary as (lon, lat) pairs.

    An irregular polygon rather than a circle or a box: a real site boundary
    follows fences and buildings, and a containment test against a rectangle
    would not exercise the polygon operator the reporting layer depends on.
    The shape is deterministic in the site's own coordinates, so it does not
    move when the simulation seed changes.
    """
    per_lat = metres_per_degree_lat()
    per_lon = metres_per_degree_lon(site.lat)
    points: list[tuple[float, float]] = []
    for index in range(vertices):
        angle = 2.0 * math.pi * index / vertices
        # Deterministic wobble so no two sites are the same shape.
        wobble = 0.78 + 0.34 * ((index * 7 + int(abs(site.lat * 100))) % 5) / 4.0
        radius = site.radius_m * wobble
        points.append(
            (
                site.lon + radius * math.cos(angle) / per_lon,
                site.lat + radius * math.sin(angle) / per_lat,
            )
        )
    return points


def polygon_literal(points: list[tuple[float, float]]) -> str:
    """Render points as a PostgreSQL ``polygon`` literal.

    x is longitude and y is latitude throughout, which is the order the
    geometric types use and the opposite of how coordinates are spoken.
    """
    body = ",".join(f"({lon:.6f},{lat:.6f})" for lon, lat in points)
    return f"({body})"


def point_literal(lon: float, lat: float) -> str:
    """Render a PostgreSQL ``point`` literal."""
    return f"({lon:.6f},{lat:.6f})"


@dataclass(frozen=True, slots=True)
class FleetMachine:
    """One machine in the registry, and everything the simulator needs about it."""

    serial: str
    machine_type: MachineType
    variant: Variant
    site: SiteGeography
    commissioned_on: date

    @property
    def item_number(self) -> str:
        """The configuration this machine was built as."""
        return self.variant.item_number

    @property
    def is_agm(self) -> bool:
        """Whether this machine carries a lead-acid pack rather than lithium."""
        return self.variant.battery.value == "agm"

    @property
    def partner_site_slug(self) -> str | None:
        """The other site of this machine's customer, if it has one."""
        for first, second in CUSTOMER_SITE_PAIRS:
            if self.site.slug == first:
                return second
            if self.site.slug == second:
                return first
        return None


def site_by_slug(slug: str) -> SiteGeography:
    """Return the geography for ``slug``, or raise :class:`KeyError`."""
    for site in SITE_GEOGRAPHY:
        if site.slug == slug:
            return site
    raise KeyError(slug)


def build_machines(catalogue: Catalogue) -> tuple[FleetMachine, ...]:
    """Build the forty machines, deterministically, from the catalogue.

    Machines are dealt round-robin across the sites rather than clustered, so
    every site carries a mix of types and no query can accidentally be answered
    by a site's identity alone.
    """
    machines: list[FleetMachine] = []
    slot = 0
    for machine_type in catalogue.machine_types:
        for index in range(MACHINES_PER_TYPE):
            variant = machine_type.variants[index % len(machine_type.variants)]
            site = SITE_GEOGRAPHY[slot % len(SITE_GEOGRAPHY)]
            slot += 1
            # Commissioned well before the telemetry window, staggered so the
            # fleet does not look like it was bought in a single afternoon.
            commissioned = date(2024, 1, 15) + timedelta(days=37 * (slot % 19))
            machines.append(
                FleetMachine(
                    serial=serial_for(machine_type, index),
                    machine_type=machine_type,
                    variant=variant,
                    site=site,
                    commissioned_on=commissioned,
                )
            )
    return tuple(machines)
