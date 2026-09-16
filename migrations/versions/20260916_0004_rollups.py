"""Hourly and daily rollups, as materialised views.

These are what the agent actually reads. Raw samples are never exposed: a
question about last week's utilisation should not be able to turn into a scan of
two and a half million rows because the planner guessed wrong.

The hour grid comes from the state intervals rather than from the samples. A
machine that is switched off emits no telemetry, so a grid built from samples
would have no row for those hours at all and "minutes off" would silently be
missing rather than zero — which is the difference between a machine that was
idle and a machine nobody can account for.

Revision ID: 0004_rollups
Revises: 0003_telemetry
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_rollups"
down_revision: str | None = "0003_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_STEP_GAP = "5 minutes"
"""Longest gap between two samples that still counts as travel.

A machine switched off in one place and on in another has not driven between
them, but consecutive rows say it did. Without this bound every power cycle adds
a straight-line jump to the distance travelled, and a machine moved by lorry
between sites adds tens of kilometres it never drove.
"""

JITTER_FLOOR_M = 2.0
"""Shortest step that counts as movement.

Consumer GPS wanders by a couple of metres while stationary. Summed over a
nine-hour shift at one sample a minute, that noise alone reports a parked
machine as having travelled about a kilometre.
"""


def upgrade() -> None:
    op.execute(f"""
        CREATE MATERIALIZED VIEW fleet.telemetry_hourly AS
        WITH bounds AS (
            SELECT date_trunc('hour', min(started_at)) AS from_hour,
                   date_trunc('hour', max(ended_at)) AS to_hour
            FROM fleet.machine_state_interval
        ),
        hours AS (
            SELECT m.serial,
                   h AS hour_start,
                   tstzrange(h, h + interval '1 hour', '[)') AS hour_range
            FROM fleet.machine m
            CROSS JOIN bounds b
            CROSS JOIN LATERAL generate_series(b.from_hour, b.to_hour, interval '1 hour') AS h
        ),
        state_minutes AS (
            SELECT hh.serial,
                   hh.hour_start,
                   sum(overlap_minutes) FILTER (WHERE s.state = 'working') AS working_minutes,
                   sum(overlap_minutes) FILTER (WHERE s.state = 'transit') AS transit_minutes,
                   sum(overlap_minutes) FILTER (WHERE s.state = 'idle_on') AS idle_on_minutes,
                   sum(overlap_minutes) FILTER (WHERE s.state = 'off') AS off_minutes
            FROM hours hh
            JOIN fleet.machine_state_interval s
              ON s.serial = hh.serial AND s.period && hh.hour_range
            CROSS JOIN LATERAL (
                SELECT extract(epoch FROM (
                    upper(s.period * hh.hour_range) - lower(s.period * hh.hour_range)
                )) / 60.0 AS overlap_minutes
            ) o
            GROUP BY hh.serial, hh.hour_start
        ),
        sample_agg AS (
            SELECT t.serial,
                   date_trunc('hour', t.ts) AS hour_start,
                   count(*) AS sample_count,
                   -- Counted here rather than left to a view over the samples:
                   -- the agent must never reach the sample table, and a view
                   -- that scanned it for every geofence question would not come
                   -- back inside the latency the reporting layer promises.
                   count(*) FILTER (
                       WHERE NOT (si.geofence @> point(t.lon, t.lat))
                   ) AS outside_geofence_samples,
                   -- Outside its own fence and outside every other site's too.
                   -- A machine working at the customer's other site is outside
                   -- its own geofence but is not lost; only this column can tell
                   -- that apart from a machine reporting from nowhere known,
                   -- and they are different questions with different answers.
                   count(*) FILTER (
                       WHERE NOT EXISTS (
                           SELECT 1 FROM fleet.site any_site
                           WHERE any_site.geofence @> point(t.lon, t.lat)
                       )
                   ) AS off_site_samples,
                   min(t.internal_temp_c) AS internal_temp_min_c,
                   max(t.internal_temp_c) AS internal_temp_max_c,
                   avg(t.internal_temp_c) AS internal_temp_avg_c,
                   max(t.battery_temp_c) AS battery_temp_max_c,
                   min(t.battery_soc_pct) AS battery_soc_min_pct,
                   (array_agg(t.battery_soc_pct ORDER BY t.ts))[1] AS battery_soc_start_pct,
                   (array_agg(t.battery_soc_pct ORDER BY t.ts DESC))[1] AS battery_soc_end_pct,
                   max(t.speed_kmh) AS speed_max_kmh
            FROM fleet.telemetry_sample t
            JOIN fleet.machine m ON m.serial = t.serial
            JOIN fleet.site si ON si.slug = m.site_slug
            GROUP BY t.serial, date_trunc('hour', t.ts)
        ),
        steps AS (
            SELECT serial,
                   date_trunc('hour', ts) AS hour_start,
                   CASE
                       WHEN ts - lag(ts) OVER w > interval '{MAX_STEP_GAP}' THEN NULL
                       ELSE fleet.haversine_m(lag(lat) OVER w, lag(lon) OVER w, lat, lon)
                   END AS step_m
            FROM fleet.telemetry_sample
            WINDOW w AS (PARTITION BY serial ORDER BY ts)
        ),
        distance AS (
            SELECT serial, hour_start, sum(step_m) AS distance_m
            FROM steps
            WHERE step_m >= {JITTER_FLOOR_M}
            GROUP BY serial, hour_start
        )
        SELECT hh.serial,
               hh.hour_start,
               coalesce(sm.working_minutes, 0)::real AS working_minutes,
               coalesce(sm.transit_minutes, 0)::real AS transit_minutes,
               coalesce(sm.idle_on_minutes, 0)::real AS idle_on_minutes,
               coalesce(sm.off_minutes, 0)::real AS off_minutes,
               coalesce(sa.sample_count, 0) AS sample_count,
               coalesce(sa.outside_geofence_samples, 0) AS outside_geofence_samples,
               coalesce(sa.off_site_samples, 0) AS off_site_samples,
               sa.internal_temp_min_c::real AS internal_temp_min_c,
               sa.internal_temp_max_c::real AS internal_temp_max_c,
               sa.internal_temp_avg_c::real AS internal_temp_avg_c,
               sa.battery_temp_max_c::real AS battery_temp_max_c,
               sa.battery_soc_min_pct::real AS battery_soc_min_pct,
               sa.battery_soc_start_pct::real AS battery_soc_start_pct,
               sa.battery_soc_end_pct::real AS battery_soc_end_pct,
               sa.speed_max_kmh::real AS speed_max_kmh,
               coalesce(d.distance_m, 0)::double precision AS distance_m
        FROM hours hh
        LEFT JOIN state_minutes sm ON sm.serial = hh.serial AND sm.hour_start = hh.hour_start
        LEFT JOIN sample_agg sa ON sa.serial = hh.serial AND sa.hour_start = hh.hour_start
        LEFT JOIN distance d ON d.serial = hh.serial AND d.hour_start = hh.hour_start
        WITH NO DATA
    """)

    # UNIQUE, so REFRESH MATERIALIZED VIEW CONCURRENTLY is available later
    # without another migration. Also the access path every reporting view uses.
    op.execute("""
        CREATE UNIQUE INDEX telemetry_hourly_pk
        ON fleet.telemetry_hourly (serial, hour_start)
    """)
    op.execute("CREATE INDEX telemetry_hourly_hour_idx ON fleet.telemetry_hourly (hour_start)")
    op.execute(
        "COMMENT ON MATERIALIZED VIEW fleet.telemetry_hourly IS "
        "'Per machine per hour: minutes in each state, sensor extremes, state of "
        "charge at the start and end of the hour, metres travelled, and samples "
        "positioned outside the machine site geofence. Rows exist for hours with no "
        "telemetry, so an hour a machine spent switched off reads as 60 minutes off "
        "rather than as a gap.'"
    )

    # Daily rolls up the hourly view rather than re-reading the samples: one pass
    # over ~86k rows instead of a second pass over millions. The averages are
    # weighted by sample_count, because an average of hourly averages silently
    # over-weights an hour with three samples against one with sixty.
    op.execute("""
        CREATE MATERIALIZED VIEW fleet.telemetry_daily AS
        SELECT serial,
               (hour_start AT TIME ZONE 'UTC')::date AS day,
               sum(working_minutes)::real AS working_minutes,
               sum(transit_minutes)::real AS transit_minutes,
               sum(idle_on_minutes)::real AS idle_on_minutes,
               sum(off_minutes)::real AS off_minutes,
               sum(sample_count) AS sample_count,
               sum(outside_geofence_samples) AS outside_geofence_samples,
               sum(off_site_samples) AS off_site_samples,
               min(internal_temp_min_c)::real AS internal_temp_min_c,
               max(internal_temp_max_c)::real AS internal_temp_max_c,
               (sum(internal_temp_avg_c::numeric * sample_count)
                   / nullif(sum(sample_count), 0))::real AS internal_temp_avg_c,
               max(battery_temp_max_c)::real AS battery_temp_max_c,
               min(battery_soc_min_pct)::real AS battery_soc_min_pct,
               (array_agg(battery_soc_start_pct ORDER BY hour_start)
                   FILTER (WHERE battery_soc_start_pct IS NOT NULL))[1]
                   AS battery_soc_start_pct,
               (array_agg(battery_soc_end_pct ORDER BY hour_start DESC)
                   FILTER (WHERE battery_soc_end_pct IS NOT NULL))[1]
                   AS battery_soc_end_pct,
               max(speed_max_kmh)::real AS speed_max_kmh,
               sum(distance_m)::double precision AS distance_m
        FROM fleet.telemetry_hourly
        GROUP BY serial, (hour_start AT TIME ZONE 'UTC')::date
        WITH NO DATA
    """)
    op.execute("CREATE UNIQUE INDEX telemetry_daily_pk ON fleet.telemetry_daily (serial, day)")
    op.execute("CREATE INDEX telemetry_daily_day_idx ON fleet.telemetry_daily (day)")
    op.execute(
        "COMMENT ON MATERIALIZED VIEW fleet.telemetry_daily IS "
        "'telemetry_hourly rolled up per calendar day (UTC). Temperature averages "
        "are weighted by sample count.'"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS fleet.telemetry_daily")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS fleet.telemetry_hourly")
