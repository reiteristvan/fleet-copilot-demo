"""Simulate what each machine did, minute by minute, for ninety days.

The shape of a cleaning fleet's day is what makes this data worth querying:
shifts, not uniform activity; batteries that drain under load and recover on
charge overnight; temperatures that climb with sustained work and fall when a
machine stands idle; positions that wander inside a site boundary rather than
sitting on a pin.

Three machines are deliberately unwell, and the faults they raise match
documents in the corpus. Those are the cases the agent should be able to find,
and the only way to know it found them for the right reason is to know exactly
where they are.

Everything random is drawn from a :class:`random.Random` seeded per machine from
the global seed and the serial, so one machine's behaviour does not depend on
how many machines were generated before it.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final

from fleet_copilot.fleet.world import (
    AS_OF,
    SEED,
    WINDOW_START,
    FleetMachine,
    metres_per_degree_lat,
    metres_per_degree_lon,
    site_by_slug,
)

SAMPLE_INTERVAL = timedelta(minutes=1)

WORKING = "working"
TRANSIT = "transit"
IDLE_ON = "idle_on"
OFF = "off"
POWERED_STATES: Final = frozenset({WORKING, TRANSIT, IDLE_ON})

# Per-minute state-of-charge cost. A machine scrubbing continuously flattens a
# pack in roughly nine hours, which is what a shift is; idling costs a third of
# that because the brushes and vacuum are the load, not the electronics.
DRAIN_WORKING_PCT = 0.118
DRAIN_TRANSIT_PCT = 0.060
DRAIN_IDLE_PCT = 0.030

OVERHEAT_FAULT_C = 60.0
"""Battery temperature at which the machine raises E-041."""

DEEP_DISCHARGE_PCT = 8.0
"""State of charge below which the machine raises E-147."""

CHARGE_RATE_PCT_PER_HOUR = 20.0
"""How fast a pack recovers on charge.

Chargers on a two-shift site are sized for opportunity charging between shifts,
not just for overnight. At a slower rate the late shift starts on whatever the
early shift left, and the whole fleet deep-discharges every day -- which makes
"which machines are finishing flat" a question about the shift pattern rather
than about the machines.
"""

FAULT_COOLDOWN = timedelta(hours=4)
"""A machine reporting the same code every minute is one fault, not sixty."""

_MONTH_BASE_TEMP_C: Final = {6: 17.0, 7: 20.5, 8: 20.0, 9: 16.5}
"""Mean daily outdoor temperature by month for the window's northern summer."""


@dataclass(frozen=True, slots=True)
class StateInterval:
    """One contiguous period in a single state."""

    serial: str
    state: str
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class FaultEvent:
    """A code the machine displayed, and the document describing it if any."""

    serial: str
    code: str
    occurred_at: datetime
    doc_id: str | None


@dataclass(slots=True)
class MachineRun:
    """Everything one machine did over the window."""

    intervals: list[StateInterval] = field(default_factory=list)
    samples: list[tuple[str, datetime, float, float, float, float, float, float, float]] = field(
        default_factory=list
    )
    faults: list[FaultEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Anomalies:
    """The three machines that are deliberately wrong, chosen by name.

    Named rather than sampled so an eval can assert that a question about
    overheating finds *this* machine. A planted case that moves when the seed
    changes cannot be asserted against.
    """

    overheating_serial: str
    deep_discharge_serial: str
    off_map_serial: str
    off_map_day: date


def runs_double_shift(serial: str) -> bool:
    """Whether this machine is worked by two shifts a day.

    A stable digest rather than the built-in hash(). Python randomises string
    hashing per process unless PYTHONHASHSEED is set, so hash() here would deal
    the fleet a different shift pattern on every run -- and the seed is supposed
    to be reproducible.
    """
    digest = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 2 == 0


def machine_rng(serial: str) -> random.Random:
    """Return the generator for ``serial``.

    Seeded from the serial rather than drawn from a shared stream, so adding a
    machine does not change the behaviour of every machine after it — which
    would otherwise rewrite the whole fleet's data for a one-row change.
    """
    return random.Random(f"{SEED}:{serial}")


def ambient_c(when: datetime, lat: float) -> float:
    """Outdoor temperature: seasonal base, daily swing, a little further north."""
    base = _MONTH_BASE_TEMP_C.get(when.month, 15.0)
    # Peak mid-afternoon, trough before dawn.
    swing = 6.0 * math.sin(2.0 * math.pi * (when.hour + when.minute / 60.0 - 9.0) / 24.0)
    latitude_adjustment = (50.0 - lat) * 0.6
    return base + swing + latitude_adjustment


def _shifts_for_day(machine: FleetMachine, day: date, rng: random.Random) -> list[tuple[int, int]]:
    """Return (start minute, length minutes) for each shift on ``day``.

    Weekends are not idle: a shopping centre and a hospital are cleaned every
    day. They are quieter, which is what makes utilisation worth reporting at
    all — a fleet that ran flat out every day would need no report.
    """
    weekend = day.weekday() >= 5
    double_shift = runs_double_shift(machine.serial)

    if weekend:
        if rng.random() > 0.70:
            return []
        start = 7 * 60 + rng.randrange(0, 90, 15)
        return [(start, rng.randrange(300, 421, 15))]

    first_start = 6 * 60 + rng.randrange(0, 60, 15)
    first_length = rng.randrange(540, 661, 15)
    shifts = [(first_start, first_length)]
    if double_shift:
        # The late shift starts after the early one hands over, not on the clock.
        # Overlapping them would be silently truncated by the interval builder,
        # which would quietly cost the fleet powered hours nobody could account
        # for -- and the handover gap is what decides how charged the pack is
        # when the second shift begins.
        handover = rng.randrange(90, 181, 15)
        shifts.append((first_start + first_length + handover, rng.randrange(330, 451, 15)))
    return shifts


def _blocks_in_shift(
    machine: FleetMachine,
    shift_start: datetime,
    shift_minutes: int,
    rng: random.Random,
) -> list[tuple[str, datetime, datetime]]:
    """Break a shift into alternating work, idle and occasional transit blocks."""
    blocks: list[tuple[str, datetime, datetime]] = []
    cursor = shift_start
    shift_end = shift_start + timedelta(minutes=shift_minutes)

    partner = machine.partner_site_slug
    # A machine moves between the customer's two sites now and then, not daily.
    transit_at: datetime | None = None
    if partner is not None and rng.random() < 0.07 and shift_minutes > 300:
        offset = rng.randrange(90, shift_minutes - 150, 15)
        transit_at = shift_start + timedelta(minutes=offset)

    moved = False
    while cursor < shift_end:
        if transit_at is not None and not moved and cursor >= transit_at:
            leg = timedelta(minutes=rng.randrange(18, 31))
            end = min(cursor + leg, shift_end)
            blocks.append((TRANSIT, cursor, end))
            cursor = end
            moved = True
            continue

        work = timedelta(minutes=rng.randrange(25, 56))
        end = min(cursor + work, shift_end)
        blocks.append((WORKING, cursor, end))
        cursor = end
        if cursor >= shift_end:
            break

        rest = timedelta(minutes=rng.randrange(4, 16))
        end = min(cursor + rest, shift_end)
        blocks.append((IDLE_ON, cursor, end))
        cursor = end

    return [block for block in blocks if block[1] < block[2]]


def build_intervals(machine: FleetMachine, rng: random.Random) -> list[StateInterval]:
    """Build a contiguous, gap-free cover of the window for one machine.

    Gap-free matters: the hourly rollup counts minutes per state, and a gap
    would read as a machine that was neither working nor off, which is not a
    state any machine can be in.
    """
    powered: list[tuple[str, datetime, datetime]] = []
    day = WINDOW_START.date()
    last_day = AS_OF.date()
    while day < last_day:
        midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
        for start_minute, length in _shifts_for_day(machine, day, rng):
            shift_start = midnight + timedelta(minutes=start_minute)
            powered.extend(_blocks_in_shift(machine, shift_start, length, rng))
        day += timedelta(days=1)

    powered.sort(key=lambda block: block[1])

    intervals: list[StateInterval] = []
    cursor = WINDOW_START
    for state, start, end in powered:
        start = max(start, cursor)
        end = min(end, AS_OF)
        if start >= end:
            continue
        if start > cursor:
            intervals.append(StateInterval(machine.serial, OFF, cursor, start))
        intervals.append(StateInterval(machine.serial, state, start, end))
        cursor = end
    if cursor < AS_OF:
        intervals.append(StateInterval(machine.serial, OFF, cursor, AS_OF))
    return intervals


def _position(
    machine: FleetMachine,
    state: str,
    progress: float,
    walk: tuple[float, float],
    rng: random.Random,
    *,
    off_map: bool,
) -> tuple[float, float, tuple[float, float]]:
    """Return (lat, lon, next walk offset) for one sample.

    Inside a site the machine wanders on a bounded random walk rather than
    jumping about: consecutive GPS fixes from a real machine are correlated, and
    independent draws would make every minute look like a teleport to the
    distance calculation.
    """
    site = machine.site
    per_lat = metres_per_degree_lat()
    per_lon = metres_per_degree_lon(site.lat)

    if state == TRANSIT and machine.partner_site_slug is not None:
        partner = site_by_slug(machine.partner_site_slug)
        lat = site.lat + (partner.lat - site.lat) * progress
        lon = site.lon + (partner.lon - site.lon) * progress
        return lat + rng.gauss(0, 2.0) / per_lat, lon + rng.gauss(0, 2.0) / per_lon, walk

    limit = site.radius_m * 0.72
    step_x = walk[0] + rng.gauss(0, 6.0)
    step_y = walk[1] + rng.gauss(0, 6.0)
    distance = math.hypot(step_x, step_y)
    if distance > limit:
        step_x *= limit / distance
        step_y *= limit / distance

    offset_m = 900.0 if off_map else 0.0
    lat = site.lat + (step_y + offset_m) / per_lat
    lon = site.lon + (step_x + offset_m) / per_lon
    return lat, lon, (step_x, step_y)


def simulate(
    machine: FleetMachine,
    anomalies: Anomalies,
    doc_links: dict[tuple[str, str], str],
) -> MachineRun:
    """Simulate one machine over the whole window."""
    rng = machine_rng(machine.serial)
    run = MachineRun()
    run.intervals = build_intervals(machine, rng)

    overheats = machine.serial == anomalies.overheating_serial
    deep_discharges = machine.serial == anomalies.deep_discharge_serial
    off_map_serial = machine.serial == anomalies.off_map_serial

    # Packs age at different rates, and the spread is wide on purpose: a fleet
    # where every machine ends its shift on the same percentage makes "which
    # machines are finishing flat" a question about the shift pattern rather
    # than about the machines. Worn packs on a double shift finish near 15%,
    # healthy ones on a single shift near 55%.
    pack_wear = rng.uniform(0.88, 1.28)

    soc = 100.0
    internal_temp = ambient_c(WINDOW_START, machine.site.lat)
    battery_temp = internal_temp
    walk = (0.0, 0.0)
    work_run = 0
    last_fault: dict[str, datetime] = {}

    def raise_fault(code: str, when: datetime) -> None:
        previous = last_fault.get(code)
        if previous is not None and when - previous < FAULT_COOLDOWN:
            return
        last_fault[code] = when
        run.faults.append(
            FaultEvent(machine.serial, code, when, doc_links.get((machine.serial, code)))
        )

    for interval in run.intervals:
        if interval.state == OFF:
            hours_off = (interval.end - interval.start).total_seconds() / 3600.0
            # Charging: a full pack takes several hours, and a short overnight
            # stop does not deliver one.
            recovery = min(100.0 - soc, hours_off * CHARGE_RATE_PCT_PER_HOUR)
            soc = min(100.0, soc + recovery)
            if deep_discharges and hours_off < 7.0:
                # An AGM pack that is never given a full cycle loses capacity,
                # so the next shift starts lower than the last one did.
                soc = min(soc, 78.0)
            ambient = ambient_c(interval.start, machine.site.lat)
            internal_temp = ambient
            battery_temp = ambient
            work_run = 0
            continue

        total_minutes = int((interval.end - interval.start).total_seconds() // 60)
        for minute in range(total_minutes):
            ts = interval.start + minute * SAMPLE_INTERVAL
            if ts >= AS_OF:
                break
            ambient = ambient_c(ts, machine.site.lat)

            if interval.state == WORKING:
                soc -= DRAIN_WORKING_PCT * pack_wear * (1.32 if deep_discharges else 1.0)
                work_run += 1
                target = ambient + 14.0 + min(work_run, 180) * 0.060
                speed = rng.uniform(3.2, 5.4)
            elif interval.state == TRANSIT:
                soc -= DRAIN_TRANSIT_PCT
                work_run = max(0, work_run - 1)
                target = ambient + 12.0
                speed = rng.uniform(9.0, 16.0)
            else:
                soc -= DRAIN_IDLE_PCT
                work_run = max(0, work_run - 2)
                target = ambient + 6.0
                speed = 0.0

            if overheats:
                # A failing cooling fan: heat goes in and does not come out.
                target += 21.0 + min(work_run, 180) * 0.05

            soc = max(0.0, min(100.0, soc))
            internal_temp += (target - internal_temp) * 0.05 + rng.gauss(0, 0.12)
            battery_target = ambient + (internal_temp - ambient) * 0.72
            battery_temp += (battery_target - battery_temp) * 0.05 + rng.gauss(0, 0.1)

            if machine.is_agm:
                voltage = 21.6 + 0.055 * soc - (0.9 if interval.state == WORKING else 0.0)
            else:
                voltage = 22.4 + 0.045 * soc - (0.3 if interval.state == WORKING else 0.0)

            progress = (minute + 1) / max(total_minutes, 1)
            off_map = off_map_serial and ts.date() == anomalies.off_map_day
            lat, lon, walk = _position(
                machine, interval.state, progress, walk, rng, off_map=off_map
            )

            run.samples.append(
                (
                    machine.serial,
                    ts,
                    round(internal_temp, 2),
                    round(soc, 2),
                    round(voltage, 2),
                    round(battery_temp, 2),
                    round(lat, 6),
                    round(lon, 6),
                    round(speed, 2),
                )
            )

            if battery_temp >= OVERHEAT_FAULT_C:
                raise_fault("E-041", ts)
            if soc <= DEEP_DISCHARGE_PCT:
                raise_fault("E-147", ts)
            if interval.state == WORKING and rng.random() < 0.00018:
                raise_fault("E-063", ts)
            if (
                interval.state == WORKING
                and machine.variant.recovery_tank_l is not None
                and rng.random() < 0.00022
            ):
                raise_fault("E-052", ts)
            if machine.variant.hopper_l is not None and rng.random() < 0.00012:
                raise_fault("E-126", ts)

    return run
