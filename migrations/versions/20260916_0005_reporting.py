"""The reporting schema, and the role the agent connects as.

Everything the agent can see is a view in `reporting`, and every one of them is
built on the rollups or the registry. There is no path from here to
`fleet.telemetry_sample`: not a slow path, not a discouraged path — no path.

That is enforced by grants rather than by convention. `copilot_ro` has USAGE on
`reporting` and SELECT on its views and nothing else at all, and because a
PostgreSQL view checks the *owner's* privileges against its base tables, the
views keep working without the role ever being able to read what they read.

Revision ID: 0005_reporting
Revises: 0004_rollups
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

from fleet_copilot.config import load_settings

revision: str = "0005_reporting"
down_revision: str | None = "0004_rollups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READONLY_ROLE = "copilot_ro"


def _password() -> str:
    """Return the local password for the read-only role.

    A development credential in the same class as POSTGRES_PASSWORD=fleet, not a
    secret: this database runs in a container on a laptop. A deployed PostgreSQL
    would authenticate this role through Entra ID instead (ADR 0002), which is
    why the password is configuration rather than a literal in the DDL.

    Interpolated into DDL, where PostgreSQL offers no bind parameter, so the
    shape is checked rather than trusted.
    """
    password = load_settings().copilot_ro_password
    if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", password):
        msg = (
            "copilot_ro_password must be 4-64 characters of letters, digits or "
            "underscore; it is interpolated into CREATE ROLE, which takes no "
            "bind parameters"
        )
        raise ValueError(msg)
    return password


def upgrade() -> None:
    op.execute(f"""
        DO $role$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READONLY_ROLE}') THEN
                CREATE ROLE {READONLY_ROLE} LOGIN PASSWORD '{_password()}';
            END IF;
        END
        $role$
    """)

    # Roles are cluster-wide, so a role left over from a previous run may carry
    # privileges this migration did not grant.
    op.execute(f"ALTER ROLE {READONLY_ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")

    # PUBLIC is a grant to every role that will ever exist, including this one.
    # Left in place on `public`, copilot_ro could read anything a later migration
    # happened to create there.
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
    op.execute("REVOKE ALL ON SCHEMA fleet FROM PUBLIC")

    op.execute("CREATE SCHEMA reporting")
    op.execute("REVOKE ALL ON SCHEMA reporting FROM PUBLIC")
    op.execute(
        "COMMENT ON SCHEMA reporting IS "
        "'Everything the copilot may read. Views over the rollups and the registry; "
        "no access to raw telemetry samples or to any base table.'"
    )

    op.execute("""
        CREATE VIEW reporting.as_of AS
        SELECT as_of FROM fleet.reporting_as_of
    """)
    op.execute(
        "COMMENT ON VIEW reporting.as_of IS "
        "'The instant reporting treats as now. Use this rather than now(): the "
        "fleet data ends here, so now() would ask about a window with nothing in it.'"
    )

    op.execute("""
        CREATE VIEW reporting.machine AS
        SELECT m.serial,
               m.commissioned_on,
               mc.item_number,
               mc.battery_chemistry,
               mc.deck,
               mc.solution_tank_l,
               mc.recovery_tank_l,
               mc.hopper_l,
               mt.code AS machine_type,
               mt.family,
               mt.name_en AS machine_name_en,
               mt.name_hu AS machine_name_hu,
               s.slug AS site_slug,
               s.name AS site_name
        FROM fleet.machine m
        JOIN fleet.machine_config mc ON mc.item_number = m.item_number
        JOIN fleet.machine_type mt ON mt.code = mc.machine_type_code
        JOIN fleet.site s ON s.slug = m.site_slug
    """)
    op.execute(
        "COMMENT ON VIEW reporting.machine IS "
        "'One row per machine, already joined to its configuration, type and site. "
        "Serials and item numbers here are the ones quoted in the document corpus.'"
    )

    op.execute("""
        CREATE VIEW reporting.site AS
        SELECT s.slug,
               s.name,
               s.address,
               s.language,
               count(m.serial) AS machine_count
        FROM fleet.site s
        LEFT JOIN fleet.machine m ON m.site_slug = s.slug
        GROUP BY s.slug, s.name, s.address, s.language
    """)

    op.execute("""
        CREATE VIEW reporting.machine_hour AS
        SELECT h.serial,
               m.site_slug,
               m.machine_type,
               h.hour_start,
               h.working_minutes,
               h.transit_minutes,
               h.idle_on_minutes,
               h.off_minutes,
               h.sample_count,
               h.outside_geofence_samples,
               h.off_site_samples,
               h.internal_temp_min_c,
               h.internal_temp_max_c,
               h.internal_temp_avg_c,
               h.battery_temp_max_c,
               h.battery_soc_min_pct,
               h.battery_soc_start_pct,
               h.battery_soc_end_pct,
               h.speed_max_kmh,
               h.distance_m
        FROM fleet.telemetry_hourly h
        JOIN reporting.machine m ON m.serial = h.serial
    """)

    op.execute("""
        CREATE VIEW reporting.machine_day AS
        SELECT d.serial,
               m.site_slug,
               m.machine_type,
               m.battery_chemistry,
               d.day,
               d.working_minutes,
               d.transit_minutes,
               d.idle_on_minutes,
               d.off_minutes,
               (d.working_minutes + d.transit_minutes + d.idle_on_minutes)::real
                   AS powered_minutes,
               round((100.0 * d.working_minutes / nullif(
                   d.working_minutes + d.transit_minutes + d.idle_on_minutes + d.off_minutes, 0
               ))::numeric, 1) AS working_pct,
               d.sample_count,
               d.outside_geofence_samples,
               d.off_site_samples,
               d.internal_temp_min_c,
               d.internal_temp_max_c,
               d.internal_temp_avg_c,
               d.battery_temp_max_c,
               d.battery_soc_min_pct,
               d.battery_soc_start_pct,
               d.battery_soc_end_pct,
               d.speed_max_kmh,
               d.distance_m
        FROM fleet.telemetry_daily d
        JOIN reporting.machine m ON m.serial = d.serial
    """)
    op.execute(
        "COMMENT ON VIEW reporting.machine_day IS "
        "'Per machine per day. working_pct is working minutes over the whole day "
        "including time switched off, which is what a fleet manager means by "
        "utilisation; powered_minutes is the denominator to use for questions "
        "about the machine rather than about the shift.'"
    )

    op.execute("""
        CREATE VIEW reporting.fault_event AS
        SELECT f.id,
               f.serial,
               m.site_slug,
               m.machine_type,
               f.code,
               e.title_en AS fault_title_en,
               e.title_hu AS fault_title_hu,
               f.occurred_at,
               f.doc_id
        FROM fleet.fault_event f
        JOIN fleet.error_code e ON e.code = f.code
        JOIN reporting.machine m ON m.serial = f.serial
    """)
    op.execute(
        "COMMENT ON COLUMN reporting.fault_event.doc_id IS "
        "'Identifier of the corpus service report describing this fault, where one "
        "exists. This is the join between the fleet data and the documents.'"
    )

    op.execute("""
        CREATE VIEW reporting.service_ticket AS
        SELECT t.id,
               t.serial,
               m.site_slug,
               m.machine_type,
               t.status,
               t.summary,
               t.opened_at,
               t.closed_at,
               o.full_name AS raised_by
        FROM fleet.service_ticket t
        JOIN reporting.machine m ON m.serial = t.serial
        LEFT JOIN fleet.operator o ON o.id = t.raised_by
    """)

    op.execute(f"GRANT USAGE ON SCHEMA reporting TO {READONLY_ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO {READONLY_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA reporting GRANT SELECT ON TABLES TO {READONLY_ROLE}"
    )
    op.execute(
        f"COMMENT ON ROLE {READONLY_ROLE} IS "
        "'The copilot. SELECT on the reporting schema and nothing else: no base "
        "tables, no raw telemetry, no DML, no DDL.'"
    )


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA reporting FROM {READONLY_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA reporting REVOKE SELECT ON TABLES FROM {READONLY_ROLE}"
    )
    op.execute(f"REVOKE ALL ON SCHEMA reporting FROM {READONLY_ROLE}")
    op.execute("DROP SCHEMA IF EXISTS reporting CASCADE")
    # The role is left in place: it is cluster-wide and may own or be granted
    # things outside this database, which a downgrade here has no business
    # dropping.
    op.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
