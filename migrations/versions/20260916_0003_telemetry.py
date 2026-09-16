"""Telemetry samples, range-partitioned by month.

One row per powered-on minute per machine. See ADR 0004 for why this is native
declarative partitioning rather than a TimescaleDB hypertable.

Partitions are declared, not created on demand. A sample whose timestamp falls
outside every declared month is rejected rather than stored, which is the right
default: a telemetry row dated 2038 is a bug in whatever produced it, and
silently accepting it into a catch-all partition is how that bug survives to
distort a rollup.

Revision ID: 0003_telemetry
Revises: 0002_state_intervals
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_telemetry"
down_revision: str | None = "0002_state_intervals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARTITION_YEAR = 2026
"""The year the corpus and the fleet timeline both live in.

Twelve empty partitions cost nothing and mean the seed can move its window
anywhere inside the year without a migration.
"""


def _months() -> list[tuple[str, str, str]]:
    """Return (suffix, inclusive start, exclusive end) for each month's partition."""
    bounds: list[tuple[str, str, str]] = []
    for month in range(1, 13):
        start = f"{PARTITION_YEAR}-{month:02d}-01"
        end = (
            f"{PARTITION_YEAR + 1}-01-01" if month == 12 else f"{PARTITION_YEAR}-{month + 1:02d}-01"
        )
        bounds.append((f"{PARTITION_YEAR}{month:02d}", start, end))
    return bounds


def upgrade() -> None:
    op.execute("""
        CREATE TABLE fleet.telemetry_sample (
            serial text NOT NULL REFERENCES fleet.machine(serial),
            ts timestamptz NOT NULL,
            internal_temp_c real NOT NULL,
            battery_soc_pct real NOT NULL,
            battery_voltage_v real NOT NULL,
            battery_temp_c real NOT NULL,
            lat double precision NOT NULL,
            lon double precision NOT NULL,
            speed_kmh real NOT NULL,

            -- The partition key has to be in the primary key. That is not a
            -- compromise here: a sample is identified by machine and instant.
            PRIMARY KEY (serial, ts),

            CONSTRAINT telemetry_soc_is_a_percentage
                CHECK (battery_soc_pct BETWEEN 0 AND 100),
            CONSTRAINT telemetry_position_on_earth
                CHECK (lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180),
            CONSTRAINT telemetry_speed_not_negative CHECK (speed_kmh >= 0),
            -- Wide enough to admit a genuinely overheating machine and narrow
            -- enough to reject a sensor reporting garbage, which is the only
            -- distinction worth drawing at write time.
            CONSTRAINT telemetry_temperatures_plausible CHECK (
                internal_temp_c BETWEEN -40 AND 150 AND battery_temp_c BETWEEN -40 AND 150
            ),
            CONSTRAINT telemetry_voltage_plausible CHECK (battery_voltage_v BETWEEN 0 AND 120)
        ) PARTITION BY RANGE (ts)
    """)

    op.execute(
        "COMMENT ON TABLE fleet.telemetry_sample IS "
        "'One sample per powered-on minute per machine, partitioned by month. "
        "Not exposed to the agent: the reporting schema offers the rollups only.'"
    )

    for suffix, start, end in _months():
        op.execute(f"""
            CREATE TABLE fleet.telemetry_sample_{suffix}
            PARTITION OF fleet.telemetry_sample
            FOR VALUES FROM ('{start}') TO ('{end}')
        """)

    # BRIN rather than btree on ts: samples arrive in time order, so a BRIN
    # summary of each 128-page range is a few kilobytes against the tens of
    # megabytes a btree over millions of rows would cost, and partition pruning
    # has already narrowed the scan to one month before this index is consulted.
    op.execute("CREATE INDEX telemetry_sample_ts_brin ON fleet.telemetry_sample USING brin (ts)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fleet.telemetry_sample")
