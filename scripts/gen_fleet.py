"""Generate the fleet registry, state intervals and telemetry, and load them.

    python scripts/gen_fleet.py            seed the database and refresh the rollups
    python scripts/gen_fleet.py --dry-run  generate and report, write nothing

Idempotent: a second run replaces the first with identical rows, in one
transaction, so a failure leaves the previous fleet in place.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import sys
import time

import psycopg

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.seed import CorpusDataError, load_catalogue
from fleet_copilot.fleet import load as fleet_load
from fleet_copilot.fleet.world import AS_OF, WINDOW_START


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="generate in memory and report the shape, without touching the database",
    )
    args = parser.parse_args()

    started = time.monotonic()
    try:
        catalogue = load_catalogue()
        if args.dry_run:
            machines = fleet_load.build_machines(catalogue)
            links = fleet_load.corpus_fault_links()
            anomalies = fleet_load.choose_anomalies(machines, links)
            print(f"{len(machines)} machines, window {WINDOW_START.date()} to {AS_OF.date()}")
            print(f"  corpus fault links : {len(links)}")
            print(f"  overheating        : {anomalies.overheating_serial}")
            print(f"  deep discharge     : {anomalies.deep_discharge_serial}")
            print(f"  off the map        : {anomalies.off_map_serial} on {anomalies.off_map_day}")
            return 0

        settings = load_settings()
        with psycopg.connect(settings.database_url) as connection:
            report = fleet_load.load_fleet(connection, catalogue)
            connection.commit()
            fleet_load.refresh_rollups(connection)
            connection.commit()
    except (CorpusDataError, SettingsError) as error:
        print(f"fleet generation failed: {error}", file=sys.stderr)
        return 2
    except psycopg.Error as error:
        print(f"database error: {error}", file=sys.stderr)
        return 3

    print(report.describe())
    print(f"  window          : {WINDOW_START.date()} to {AS_OF.date()}")
    print(f"  elapsed         : {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
