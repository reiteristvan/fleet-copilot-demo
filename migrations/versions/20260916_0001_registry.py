"""Fleet registry: the tables the agent joins documents against.

Structure only. Every row in here is written by scripts/gen_fleet.py, which
derives the registry from the same data/catalogue.yaml the document corpus was
generated from — so a serial quoted in a service report is a serial the database
knows, and a site named in a handover note is a site with machines on it.

Revision ID: 0001_registry
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_registry"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # btree_gist lets a GiST exclusion constraint mix plain equality (serial)
    # with range overlap, which 0002 needs. Created here because it is the only
    # extension the fleet schema requires and this is the first migration.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE SCHEMA IF NOT EXISTS fleet")
    op.execute(
        "COMMENT ON SCHEMA fleet IS "
        "'Operational fleet data. Base tables; the agent reads the reporting schema.'"
    )

    op.execute("""
        CREATE TABLE fleet.reporting_as_of (
            only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row),
            as_of timestamptz NOT NULL
        )
    """)
    op.execute(
        "COMMENT ON TABLE fleet.reporting_as_of IS "
        "'The instant reporting treats as now. One row, enforced by the primary key. "
        'The telemetry window ends here, so "last 7 days" means the same thing '
        "whenever it is asked.'"
    )

    op.execute("""
        CREATE TABLE fleet.machine_type (
            code text PRIMARY KEY,
            family text NOT NULL,
            name_en text NOT NULL,
            name_hu text NOT NULL,
            CONSTRAINT machine_type_code_shape CHECK (code ~ '^[A-Z]{2,4}-[0-9]{2,3}[A-Z]?$'),
            CONSTRAINT machine_type_family_known CHECK (family IN (
                'scrubber_dryer_walk_behind', 'scrubber_dryer_ride_on',
                'sweeper_ride_on', 'vacuum_sweeper', 'single_disc'
            ))
        )
    """)
    op.execute(
        "COMMENT ON TABLE fleet.machine_type IS "
        "'A machine model, e.g. SD-50B. Manuals are written per type.'"
    )

    op.execute("""
        CREATE TABLE fleet.machine_config (
            item_number text PRIMARY KEY,
            machine_type_code text NOT NULL REFERENCES fleet.machine_type(code),
            battery_chemistry text NOT NULL,
            deck text NOT NULL,
            solution_tank_l integer,
            recovery_tank_l integer,
            hopper_l integer,
            CONSTRAINT machine_config_item_number_shape
                CHECK (item_number ~ '^[0-9]\\.[0-9]{3}-[0-9]{3}\\.[0-9]$'),
            CONSTRAINT machine_config_battery_known
                CHECK (battery_chemistry IN ('lithium-ion', 'agm')),
            CONSTRAINT machine_config_deck_known CHECK (deck IN ('brush', 'pad')),
            CONSTRAINT machine_config_capacities_positive CHECK (
                coalesce(solution_tank_l, 1) > 0
                AND coalesce(recovery_tank_l, 1) > 0
                AND coalesce(hopper_l, 1) > 0
            ),
            -- The same rule the corpus models enforce: a recovery tank takes
            -- back everything the solution tank puts down plus the soil lifted
            -- with it, so it is never the smaller of the two.
            CONSTRAINT machine_config_recovery_holds_solution CHECK (
                solution_tank_l IS NULL
                OR recovery_tank_l IS NULL
                OR recovery_tank_l >= solution_tank_l
            )
        )
    """)
    op.execute(
        "COMMENT ON TABLE fleet.machine_config IS "
        "'An orderable configuration of a machine type, identified by item number. "
        "Battery chemistry and deck differ between configurations of one type, and "
        "so do the figures a manual quotes.'"
    )

    op.execute("""
        CREATE TABLE fleet.site (
            slug text PRIMARY KEY,
            name text NOT NULL,
            address text NOT NULL,
            language text NOT NULL,
            centre point NOT NULL,
            geofence polygon NOT NULL,
            CONSTRAINT site_slug_shape CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            CONSTRAINT site_language_known CHECK (language IN ('en', 'hu')),
            -- A centre outside its own fence would put every position generated
            -- around it outside the geofence, making the planted
            -- outside-geofence anomaly indistinguishable from a broken site.
            CONSTRAINT site_centre_inside_geofence CHECK (geofence @> centre)
        )
    """)
    op.execute("CREATE INDEX site_geofence_gist ON fleet.site USING gist (geofence)")
    op.execute(
        "COMMENT ON COLUMN fleet.site.geofence IS "
        "'Site boundary as a native polygon of (lon, lat) points. Containment is "
        "the built-in polygon @> point operator; no PostGIS.'"
    )

    op.execute("""
        CREATE TABLE fleet.machine (
            serial text PRIMARY KEY,
            item_number text NOT NULL REFERENCES fleet.machine_config(item_number),
            site_slug text NOT NULL REFERENCES fleet.site(slug),
            commissioned_on date NOT NULL,
            CONSTRAINT machine_serial_shape CHECK (serial ~ '^[A-Z0-9]{4,10}-[0-9]{4}-[0-9]{5}$')
        )
    """)
    op.execute("CREATE INDEX machine_site_idx ON fleet.machine (site_slug)")
    op.execute("CREATE INDEX machine_config_idx ON fleet.machine (item_number)")
    op.execute(
        "COMMENT ON TABLE fleet.machine IS "
        "'A physical machine, identified by serial. Serials quoted in service and "
        "fault reports in the document corpus resolve here.'"
    )

    op.execute("""
        CREATE TABLE fleet.operator (
            id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            full_name text NOT NULL,
            site_slug text NOT NULL REFERENCES fleet.site(slug),
            UNIQUE (full_name, site_slug)
        )
    """)

    op.execute("""
        CREATE TABLE fleet.error_code (
            code text PRIMARY KEY,
            title_en text NOT NULL,
            title_hu text NOT NULL,
            CONSTRAINT error_code_shape CHECK (code ~ '^E-[0-9]{3}$')
        )
    """)
    op.execute(
        "COMMENT ON TABLE fleet.error_code IS "
        "'Display codes, the same twelve the error-code reference document lists.'"
    )

    op.execute("""
        CREATE TABLE fleet.error_code_machine_type (
            code text NOT NULL REFERENCES fleet.error_code(code),
            machine_type_code text NOT NULL REFERENCES fleet.machine_type(code),
            PRIMARY KEY (code, machine_type_code)
        )
    """)
    op.execute(
        "COMMENT ON TABLE fleet.error_code_machine_type IS "
        "'Which machine types can raise a given code. The same code may mean "
        "something else on a type not listed here.'"
    )

    op.execute("""
        CREATE TABLE fleet.service_ticket (
            id text PRIMARY KEY,
            serial text NOT NULL REFERENCES fleet.machine(serial),
            opened_at timestamptz NOT NULL,
            closed_at timestamptz,
            status text NOT NULL,
            summary text NOT NULL,
            raised_by integer REFERENCES fleet.operator(id),
            CONSTRAINT service_ticket_status_known
                CHECK (status IN ('open', 'in_progress', 'closed')),
            CONSTRAINT service_ticket_closed_after_opened
                CHECK (closed_at IS NULL OR closed_at >= opened_at),
            -- A closed ticket with no closing time cannot be aged, and an open
            -- one with a closing time is a contradiction rather than a nuance.
            CONSTRAINT service_ticket_closure_consistent CHECK (
                (status = 'closed') = (closed_at IS NOT NULL)
            )
        )
    """)
    op.execute("CREATE INDEX service_ticket_serial_idx ON fleet.service_ticket (serial)")
    op.execute("CREATE INDEX service_ticket_open_idx ON fleet.service_ticket (status, opened_at)")

    op.execute("""
        CREATE TABLE fleet.fault_event (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            serial text NOT NULL REFERENCES fleet.machine(serial),
            code text NOT NULL REFERENCES fleet.error_code(code),
            occurred_at timestamptz NOT NULL,
            doc_id text,
            UNIQUE (serial, code, occurred_at)
        )
    """)
    op.execute(
        "CREATE INDEX fault_event_serial_time_idx ON fleet.fault_event (serial, occurred_at)"
    )
    op.execute("CREATE INDEX fault_event_code_time_idx ON fleet.fault_event (code, occurred_at)")
    op.execute(
        "COMMENT ON COLUMN fleet.fault_event.doc_id IS "
        "'The corpus service report describing this fault, where one exists. Not a "
        "foreign key: the documents live in Blob Storage and a text identifier is "
        "the whole of the contract between the two halves of the system.'"
    )

    # Distance on a sphere. PostgreSQL's built-in point distance is Euclidean in
    # degrees, which is not metres and is wrong by a factor that varies with
    # latitude, so the rollups cannot use it. IMMUTABLE and PARALLEL SAFE so it
    # can be used in a generated column or an index if that is ever wanted.
    op.execute("""
        CREATE FUNCTION fleet.haversine_m(
            lat1 double precision, lon1 double precision,
            lat2 double precision, lon2 double precision
        ) RETURNS double precision
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        AS $fn$
            SELECT 6371000.0 * 2.0 * asin(sqrt(
                power(sin(radians(lat2 - lat1) / 2.0), 2)
                + cos(radians(lat1)) * cos(radians(lat2))
                * power(sin(radians(lon2 - lon1) / 2.0), 2)
            ))
        $fn$
    """)
    op.execute(
        "COMMENT ON FUNCTION fleet.haversine_m IS "
        "'Great-circle distance in metres between two WGS84 points.'"
    )


def downgrade() -> None:
    doubles = ", ".join(["double precision"] * 4)
    op.execute(f"DROP FUNCTION IF EXISTS fleet.haversine_m({doubles})")
    for table in (
        "fault_event",
        "service_ticket",
        "error_code_machine_type",
        "error_code",
        "operator",
        "machine",
        "site",
        "machine_config",
        "machine_type",
        "reporting_as_of",
    ):
        op.execute(f"DROP TABLE IF EXISTS fleet.{table}")
    op.execute("DROP SCHEMA IF EXISTS fleet")
